import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, Gdk, Pango

import threading
import os
import re
import requests
import json
import traceback

class ChatbotWindow(Gtk.Window):
    def __init__(self):
        Gtk.Window.__init__(self, title="RAG Chatbot")
        self.set_default_size(500, 400)
        self.set_border_width(10)
        self.apply_css()
        
        self.api_base_url = "http://127.0.0.1:8000"
        self.is_ready = False

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.add(vbox)

        action_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        vbox.pack_start(action_bar, False, False, 0)

        clear_button = Gtk.Button.new_from_icon_name("edit-clear-all-symbolic", Gtk.IconSize.MENU)
        clear_button.set_tooltip_text("Clear conversation")
        clear_button.connect("clicked", self.on_clear_button_clicked)
        action_bar.pack_start(clear_button, False, False, 0)
        
        save_button = Gtk.Button.new_from_icon_name("document-save-symbolic", Gtk.IconSize.MENU)
        save_button.set_tooltip_text("Save conversation")
        save_button.connect("clicked", self.on_save_button_clicked)
        action_bar.pack_start(save_button, False, False, 0)
        
        file_button = Gtk.Button.new_from_icon_name("document-open-symbolic", Gtk.IconSize.MENU)
        file_button.set_tooltip_text("Select Knowledge Base (PDF)")
        file_button.connect("clicked", self.on_file_button_clicked)
        action_bar.pack_start(file_button, False, False, 0)

        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_hexpand(True)
        scrolled_window.set_vexpand(True)
        vbox.pack_start(scrolled_window, True, True, 0)
        scrolled_window.get_style_context().add_class("chat-history")

        self.message_view = Gtk.TextView()
        self.message_view.set_editable(False)
        self.message_view.set_cursor_visible(False)
        self.message_view.set_left_margin(10)
        self.message_view.set_right_margin(10)
        self.message_view.set_wrap_mode(Gtk.WrapMode.WORD)
        scrolled_window.add(self.message_view)

        self.message_buffer = self.message_view.get_buffer()
        self.tag_user = self.message_buffer.create_tag(
            "user", foreground="#333333", weight=600, justification=Gtk.Justification.LEFT
        )
        self.tag_chatbot = self.message_buffer.create_tag(
            "chatbot", foreground="#00796b", weight=400, justification=Gtk.Justification.LEFT
        )
        self.tag_system = self.message_buffer.create_tag(
            "system", foreground="#999999", style=Pango.Style.ITALIC, justification=Gtk.Justification.CENTER
        )
        
        input_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        vbox.pack_start(input_box, False, False, 0)

        self.input_entry = Gtk.Entry()
        self.input_entry.set_hexpand(True)
        self.input_entry.get_style_context().add_class("input-entry")
        input_box.pack_start(self.input_entry, True, True, 0)
        self.input_entry.connect("activate", self.on_send_button_clicked)

        self.send_button = Gtk.Button.new_from_icon_name("mail-send-receive-symbolic", Gtk.IconSize.MENU)
        self.send_button.set_tooltip_text("Send message")
        self.send_button.connect("clicked", self.on_send_button_clicked)
        input_box.pack_start(self.send_button, False, False, 0)

        self.spinner = Gtk.Spinner()
        input_box.pack_start(self.spinner, False, False, 0)

        self.append_message("Select a PDF to begin.", "system")
        self.input_entry.set_sensitive(False)
        self.send_button.set_sensitive(False)

    def on_file_button_clicked(self, widget):
        dialog = Gtk.FileChooserDialog(
            "Please choose a PDF file", self, Gtk.FileChooserAction.OPEN,
            (Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, "Select", Gtk.ResponseType.ACCEPT)
        )
        
        filter_pdf = Gtk.FileFilter()
        filter_pdf.set_name("PDF files")
        filter_pdf.add_mime_type("application/pdf")
        dialog.add_filter(filter_pdf)
        
        response = dialog.run()
        if response == Gtk.ResponseType.ACCEPT:
            pdf_path = dialog.get_filename()
            self.input_entry.set_sensitive(False)
            self.send_button.set_sensitive(False)
            self.append_message(f"Uploading {os.path.basename(pdf_path)}...", "system")
            self.spinner.start()
            
            thread = threading.Thread(target=self.send_pdf_to_server, args=(pdf_path,))
            thread.daemon = True
            thread.start()
            
        dialog.destroy()

    def send_pdf_to_server(self, pdf_path):
        try:
            with open(pdf_path, 'rb') as f:
                files = {'file': (os.path.basename(pdf_path), f, 'application/pdf')}
                response = requests.post(f"{self.api_base_url}/upload-pdf", files=files)
                response.raise_for_status() # Raises an HTTPError for bad responses (4xx or 5xx)
            
            GLib.idle_add(self.on_server_response, response.json())
        except requests.exceptions.RequestException as e:
            GLib.idle_add(self.on_server_error, f"Failed to connect to server: {e}")
        except Exception as e:
            GLib.idle_add(self.on_server_error, f"An unexpected error occurred: {e}")

    def on_send_button_clicked(self, widget):
        user_text = self.input_entry.get_text()
        if not user_text:
            return

        self.append_message(user_text, "user")
        self.input_entry.set_text("")
        self.input_entry.set_sensitive(False)
        self.send_button.set_sensitive(False)
        self.spinner.start()

        thread = threading.Thread(target=self.send_query_to_server, args=(user_text,))
        thread.daemon = True
        thread.start()

    def send_query_to_server(self, user_text):
        try:
            headers = {'Content-Type': 'application/json'}
            data = json.dumps({'text': user_text})
            response = requests.post(f"{self.api_base_url}/query-chatbot", data=data, headers=headers)
            response.raise_for_status()
            GLib.idle_add(self.on_server_response, response.json())
        except requests.exceptions.RequestException as e:
            GLib.idle_add(self.on_server_error, f"Failed to connect to server: {e}")

    def on_server_response(self, response_data):
        if response_data['status'] == 'success':
            if 'response' in response_data and response_data['response']:
                formatted_response = self.format_response(response_data['response'])
                self.append_message(formatted_response, "chatbot")
            else:
                self.append_message(response_data['message'], "system")
            self.is_ready = True
        else:
            self.append_message(f"Server error: {response_data['message']}", "system")
            self.is_ready = False
        
        self.spinner.stop()
        self.input_entry.set_sensitive(self.is_ready)
        self.send_button.set_sensitive(self.is_ready)

    def on_server_error(self, message):
        self.append_message(message, "system")
        self.spinner.stop()
        self.is_ready = False
        self.input_entry.set_sensitive(self.is_ready)
        self.send_button.set_sensitive(self.is_ready)

    def format_response(self, text):
        text = re.sub(r'\n{2,}', '\n', text)
        text = re.sub(r'(\d+\.)\s', r'\n\1 ', text)
        text = re.sub(r'([-*])\s', r'\n\1 ', text)
        return text

    def on_clear_button_clicked(self, widget):
        self.message_buffer.set_text("")
        self.append_message("Chat history cleared.", "system")

    def on_save_button_clicked(self, widget):
        dialog = Gtk.FileChooserDialog("Save Conversation", self, Gtk.FileChooserAction.SAVE,
            (Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, "Save", Gtk.ResponseType.ACCEPT))
        dialog.set_current_name("chatbot_conversation.txt")
        response = dialog.run()
        if response == Gtk.ResponseType.ACCEPT:
            filename = dialog.get_filename()
            with open(filename, "w") as f:
                start_iter, end_iter = self.message_buffer.get_bounds()
                text = self.message_buffer.get_text(start_iter, end_iter, True)
                f.write(text)
        dialog.destroy()

    def append_message(self, message, tag_name):
        end_iter = self.message_buffer.get_end_iter()
        if tag_name == "user":
            self.message_buffer.insert(end_iter, "You: ")
        elif tag_name == "chatbot":
            self.message_buffer.insert(end_iter, "Chatbot: ")
        elif tag_name == "system":
            self.message_buffer.insert(end_iter, "System: ")
        self.message_buffer.insert(end_iter, message + "\n")
        end_iter = self.message_buffer.get_end_iter()
        start_iter, end_iter = self.message_buffer.get_bounds()
        self.message_buffer.apply_tag_by_name(tag_name, self.message_buffer.get_iter_at_offset(start_iter.get_offset()), end_iter)
        self.message_view.scroll_to_iter(end_iter, 0.0, True, 0.0, 1.0)
        return False

    def apply_css(self):
        style_provider = Gtk.CssProvider()
        try:
            style_provider.load_from_path('style.css')
            Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), style_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        except Exception as e:
            print(f"Failed to load CSS: {e}")

if __name__ == "__main__":
    win = ChatbotWindow()
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()