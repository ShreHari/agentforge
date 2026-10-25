from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from pydantic import BaseModel, Field

load_dotenv()  # copies each line of .env into an environment variable

# "provider:model". temperature=0 makes answers as consistent as possible.
model = init_chat_model("google_genai:gemini-3.5-flash", temperature=0)

# --- Part 1: plain text in, text out ---
reply = model.invoke("In one sentence, what is LangGraph?")
print(reply.text)

# --- Part 2: structured output ---


class Skills(BaseModel):
    """Technical skills a job ad asks for."""
    skills: list[str] = Field(
        description="each technical skill, a few words each")


structured_model = model.with_structured_output(Skills)
result = structured_model.invoke(open("samples/job_ad.txt").read())
print(result)
print(type(result))
