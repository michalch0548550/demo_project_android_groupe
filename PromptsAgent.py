"""
דבר ראשון כל אחת צריכה להתקין אצלה בטרמינל את הפקודה
pip install langchain-google-genai langchain-core
pip install langchain-google-genai
"""

from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
import os
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser

os.environ["GOOGLE_API_KEY"] = os.getenv("GOOGLE_API_KEY", "")

llm = ChatGoogleGenerativeAI(model="gemma-4-26b-a4b-it", temperature=0.4)
meta_prompt_template = PromptTemplate.from_template(
    "User goal: {goal}\n\n"
    "You are an expert technical prompt engineer. Your task is to convert the user's goal into a single, concise, and direct instructional prompt for an AI coding assistant.\n"
    "The prompt should be highly technical, straight to the point, and written in English.\n"
    "Return ONLY the generated prompt, without any conversational text or quotes."
)
prompt_generator_chain = meta_prompt_template | llm | StrOutputParser()
execution_prompt_template = PromptTemplate.from_template("{generated_prompt}")
execution_chain = execution_prompt_template | llm | StrOutputParser()
user_goal = "Install AppsFlyer's SDK in my app using their MCP"
dynamic_prompt = prompt_generator_chain.invoke({"goal": user_goal})
print(dynamic_prompt)
