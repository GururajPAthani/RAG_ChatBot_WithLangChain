import os
import re
import hashlib
from fastapi import FastAPI, UploadFile, File, HTTPException
from pydantic import BaseModel
import shutil
import uvicorn
import aiofiles
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.llms import Ollama
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain_core.prompts import ChatPromptTemplate
from langchain_community.vectorstores import Chroma
from langchain_community.embeddings import OllamaEmbeddings
from langchain.chains import create_retrieval_chain
import uuid
import threading
import asyncio

# --- Global State and API Setup ---
app = FastAPI()
db_lock = threading.Lock()
llm_model = Ollama(model="gemma3:1b")
embeddings_model = OllamaEmbeddings(model="nomic-embed-text")
persist_directory = "./chroma_db"

# --- Models for API Request/Response ---
class Query(BaseModel):
    text: str

class Response(BaseModel):
    status: str
    message: str
    response: str = None

# --- Helper Functions ---
def get_document_signature(file_path):
    hasher = hashlib.sha256()
    with open(file_path, 'rb') as f:
        while chunk := f.read(4096):
            hasher.update(chunk)
    return hasher.hexdigest()

def initialize_retriever_for_file(file_path: str):
    try:
        os.makedirs(persist_directory, exist_ok=True)
        signature_file = os.path.join(persist_directory, "doc_signature.txt")
        current_signature = get_document_signature(file_path)

        load_from_disk = False
        if os.path.exists(signature_file):
            with open(signature_file, 'r') as f:
                saved_signature = f.read()
            if saved_signature == current_signature:
                load_from_disk = True

        if load_from_disk:
            print("Loading existing embeddings...")
            vector_store = Chroma(persist_directory=persist_directory, embedding_function=embeddings_model)
        else:
            print("Creating new embeddings...")
            loader = PyPDFLoader(file_path)
            docs = loader.load()
            text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
            all_splits = text_splitter.split_documents(docs)
            vector_store = Chroma.from_documents(documents=all_splits, embedding=embeddings_model, persist_directory=persist_directory)
            with open(signature_file, 'w') as f:
                f.write(current_signature)

        return vector_store.as_retriever()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to process PDF: {str(e)}")

# --- API Endpoints ---
@app.post("/upload-pdf")
async def upload_pdf(file: UploadFile = File(...)):
    if not file.filename.endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed.")

    file_id = str(uuid.uuid4())
    temp_file_path = f"/tmp/{file_id}.pdf"
    
    try:
        # Save uploaded file to a temporary location
        async with aiofiles.open(temp_file_path, 'wb') as out_file:
            while content := await file.read(1024):
                await out_file.write(content)

        # Process the file and create/load embeddings
        with db_lock:
            retriever = initialize_retriever_for_file(temp_file_path)
        
        # In a real app, you would associate the retriever with a user session or ID
        # For this example, we just signal success
        
        return Response(status="success", message=f"PDF '{file.filename}' processed successfully. Chat is ready.")
    finally:
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)

@app.post("/query-chatbot", response_model=Response)
async def query_chatbot(query: Query):
    try:
        # Re-initialize the retrieval chain on each request to be stateless
        # In a real app, this would be more efficient, e.g., using a dedicated
        # service for document retrieval.
        with db_lock:
            vector_store = Chroma(persist_directory=persist_directory, embedding_function=embeddings_model)
            retriever = vector_store.as_retriever()

        if not retriever:
            raise HTTPException(status_code=404, detail="No knowledge base loaded. Please upload a PDF first.")

        prompt = ChatPromptTemplate.from_template("""Answer the user's question based on the provided context:
<context>
{context}
</context>
Question: {input}""")
        
        document_chain = create_stuff_documents_chain(llm_model, prompt)
        retrieval_chain = create_retrieval_chain(retriever, document_chain)
        
        # Run the chain in a thread pool to avoid blocking the event loop
        response = await asyncio.to_thread(retrieval_chain.invoke, {"input": query.text})
        
        return Response(status="success", message="Query processed.", response=response['answer'])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An error occurred: {str(e)}")




if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)