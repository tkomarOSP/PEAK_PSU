from pathlib import Path
from langchain.agents import Tool, create_react_agent
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph
from langgraph.checkpoint.sqlite import SqliteSaver
from capellambse import model as capellambse_model
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from IPython.display import display, Markdown
from ipywidgets import widgets
from jupyter_ui_poll import ui_events
import time
import os
import PyPDF2
from docx import Document


def get_api_key():
    """Retrieve the OpenAI API key from a hidden file."""
    home_dir = Path.home()
    key_file = home_dir / ".secrets" / "openai_api_key.txt"

    if not key_file.exists():
        raise FileNotFoundError(
            f"API Key file not found at {key_file}. "
            "Please ensure the key is saved correctly."
        )

    with key_file.open("r") as f:
        api_key = f.read().strip()

    if not api_key:
        raise ValueError("API Key file is empty. Provide a valid API key.")

    return api_key


class CapellaOpenAIAgent:
    ALLOWED_EXTENSIONS = {'.yaml', '.yml', '.txt', '.xml', '.json', '.html', '.pdf', '.docx', '.sysml'}
    TEXT_BASED_EXTS = {'.yaml', '.yml', '.txt', '.xml', '.json', '.html', '.sysml'}
    PDF_EXTS = {'.pdf'}
    DOCX_EXTS = {'.docx'}

    def __init__(self, model_instance, yaml_content=None, api_key=None):
        try:
            self.api_key = get_api_key()
            print("OpenAI API Key retrieved successfully.")
        except (FileNotFoundError, ValueError) as e:
            print(e)
            sys.exit(1)
        self.capella_model = model_instance
        self.yaml_content = yaml_content or ""
        self.llm = ChatOpenAI(model_name="gpt-4o", api_key=self.api_key)
        self.tools = self._define_tools()
       

        # Use create_react_agent with LangGraph 0.4.8
        self.react_agent = create_react_agent(
            model=self.llm,
            tools=self.tools,
            name="capella-agent",
            version="v2"
        )

        graph = StateGraph(self.react_agent.config.schema)
        graph.add_node("agent", self.react_agent)
        graph.set_entry_point("agent")
        self.agent_executor = graph.compile()

        self.chat_active = True
        self.file_context = ""
        self.graph_state = [SystemMessage(content="You are an assistant for Capella system model analysis.")]

    def _define_tools(self):
        self._tool_methods = {
            "create_requirement": self.create_requirement,
            "create_function": self.create_function,
            "create_actor": self.create_actor,
            "update_description": self.update_description,
            "update_exchange_name": self.update_exchange_name
        }
        return [
            Tool(name="Create Requirement", func=self.create_requirement, description="Create a requirement with long_name, text, and parent_uuid."),
            Tool(name="Create Function", func=self.create_function, description="Create a function with name, description, and parent_uuid."),
            Tool(name="Create Actor", func=self.create_actor, description="Create an actor of a given type (entity, component, node) with name, description, and parent_uuid."),
            Tool(name="Update Description", func=self.update_description, description="Update the description of an object by uuid."),
            Tool(name="Update Exchange Name", func=self.update_exchange_name, description="Update the name of a component exchange or physical link using uuid.")
        ]

    def create_requirement(self, long_name: str, text: str, parent_uuid: str):
        try:
            parent = self.capella_model.by_uuid(parent_uuid)
            return parent.requirements.create(long_name=long_name, text=text)
        except Exception as e:
            return f"❌ Error: {e}"

    def create_function(self, name: str, description: str, parent_uuid: str):
        try:
            parent = self.capella_model.by_uuid(parent_uuid)
            return parent.functions.create(name=name, description=description)
        except Exception as e:
            return f"❌ Error: {e}"

    def create_actor(self, type_: str, name: str, description: str, parent_uuid: str):
        try:
            parent = self.capella_model.by_uuid(parent_uuid)
            return getattr(parent, type_.lower() + 's').create(name=name, description=description)
        except Exception as e:
            return f"❌ Error: {e}"

    def update_description(self, uuid: str, description: str):
        try:
            obj = self.capella_model.by_uuid(uuid)
            obj.description = description
            return obj
        except Exception as e:
            return f"❌ Error: {e}"

    def update_exchange_name(self, uuid: str, name: str):
        try:
            obj = self.capella_model.by_uuid(uuid)
            obj.name = name
            return obj
        except Exception as e:
            return f"❌ Error: {e}"

    def run(self, prompt: str):
        input_payload = {
            "input": prompt,
            "chat_history": self.graph_state
        }
        self.graph_state.append(HumanMessage(content=prompt))
        output_msg = self.agent_executor.invoke(input_payload)
        self.graph_state.append(AIMessage(content=output_msg["output"]))
        return output_msg["output"]

    def submit_prompt(self, prompt):
        full_prompt = ""
        if self.yaml_content:
            full_prompt += f"Here is the YAML file:\n---\n{self.yaml_content}\n---\n\n"
        if self.file_context:
            full_prompt += f"Here are some added file contents:\n---\n{self.file_context}\n---\n\n"
        full_prompt += prompt

        display(Markdown(f"**User:** {full_prompt}"))
        response = self.run(full_prompt)
        display(Markdown(f"**Agent Response:** {response}"))
        return response

    def add_text_file_to_memory(self, filepath):
        ext = os.path.splitext(filepath)[1].lower()
        if ext not in self.ALLOWED_EXTENSIONS:
            return f"❌ Unsupported file type: {ext}"

        if ext in self.TEXT_BASED_EXTS:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
        elif ext in self.PDF_EXTS:
            with open(filepath, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                content = "\n".join(page.extract_text() for page in reader.pages if page.extract_text())
        elif ext in self.DOCX_EXTS:
            doc = Document(filepath)
            content = "\n".join([p.text for p in doc.paragraphs])
        else:
            content = ""

        self.file_context += f"\nFile `{filepath}` was added for analysis:\n---\n{content}\n---\n"
        return f"✅ File `{filepath}` added to prompt context."

    def interactive_chat(self):
        print("Starting interactive agent chat...")
        chat_history = widgets.Output()
        user_input = widgets.Textarea(
            placeholder="Type your prompt...",
            rows=3,
            layout=widgets.Layout(width="100%", border="2px solid #4A90E2", border_radius="8px",
                                  padding="12px", background_color="#F7F9FC", 
                                  box_shadow="3px 3px 10px rgba(0, 0, 0, 0.1)")
        )
        send_button = widgets.Button(description="Execute", button_style="primary")
        exit_button = widgets.Button(description="Exit", button_style="danger")
        file_list = [f for f in os.listdir(os.getcwd()) if os.path.isfile(f) and os.path.splitext(f)[1].lower() in self.ALLOWED_EXTENSIONS]
        file_dropdown = widgets.Dropdown(
            options=[""] + file_list,
            description="Load file:",
            layout=widgets.Layout(width="auto")
        )

        def load_file(_):
            filename = file_dropdown.value
            if not filename:
                return
            msg = self.add_text_file_to_memory(filename)
            with chat_history:
                display(Markdown(msg))

        file_dropdown.observe(load_file, names="value")

        def send_message(_):
            prompt = user_input.value.strip()
            if not prompt:
                return
            with chat_history:
                self.submit_prompt(prompt)
            user_input.value = ""

        def exit_chat(_):
            self.chat_active = False

        send_button.on_click(send_message)
        exit_button.on_click(exit_chat)
        display(chat_history, user_input, widgets.HBox([send_button, exit_button]), file_dropdown)
        print("Waiting for chat interactions...")
        with ui_events() as poll:
            while self.chat_active:
                poll(10)
                time.sleep(1)
