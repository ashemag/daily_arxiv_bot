import arxiv
import datetime
from datetime import datetime, timedelta
from openai import OpenAI
import pytz
import json
import os
from slack_sdk import WebClient
import requests
from bs4 import BeautifulSoup
from typing import List
import modal
import ast

stub = modal.Stub()

slack_client = WebClient(token=os.environ["SLACK_API_BOT_KEY"])


def format_human_readable(datetime_str: str) -> str:
    dt = datetime.strptime(datetime_str, "%Y-%m-%d %H:%M:%S%z")
    readable_format = dt.strftime("%B %d, %Y, %H:%M")

    return readable_format


def get_slack_channel_from_name(name: str):
    response = slack_client.conversations_list(types="public_channel,private_channel")
    if response["ok"]:
        channels = response["channels"]
        for channel in channels:
            if channel["name"] == name:
                print(channel["id"])
                return channel["id"]
    else:
        print(f"Error: {response['error']}")


def is_within_last_24_hours(dt_str: str):
    given_dt = datetime.fromisoformat(dt_str)
    current_dt = datetime.now(pytz.utc)
    time_diff = current_dt - given_dt

    return time_diff < timedelta(days=1)


def create_slack_block(text: str):
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def call_openai(
    user_prompt: str,
    system_prompt: str,
    json_response: bool = False,
):
    client = OpenAI()
    args = {
        "model": "gpt-4-turbo",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.0,
    }
    if json_response:
        args["response_format"] = {"type": "json_object"}

    completion = client.chat.completions.create(**args)
    output = completion.choices[0].message.content
    return json.loads(output) if json_response else output


def create_slack_link(text: str, link: str) -> str:
    return f"<{link}|{text}>"


def get_possible_university_affiliations(url: str) -> List:
    response = requests.get(url)
    if response.status_code == 200:
        soup = BeautifulSoup(response.text, "html.parser")
        spans = soup.find_all("span", class_="ltx_role_affiliation")
        return sorted(
            [
                item
                for item in list(set([span.get_text(strip=True) for span in spans]))
                if item != ""
            ]
        )

    return []


def get_paper_text(pdf_url: str) -> str:
    if "pdf" not in pdf_url.lower():
        return ""
    import PyPDF2
    import io

    response = requests.get(pdf_url)
    pdf_file = io.BytesIO(response.content)
    reader = PyPDF2.PdfReader(pdf_file)
    text = ""
    for page in reader.pages:
        text += page.extract_text()

    return text


# @stub.function(
#     schedule=modal.Cron("30 15 * * 1-5"),
#     image=modal.Image.debian_slim().pip_install(
#         [
#             "slack-sdk",
#             "python-dotenv",
#             "requests",
#             "emoji",
#             "openai",
#             "arxiv",
#             "pytz",
#             "bs4",
#             "PyPDF2",
#         ]
#     ),
#     secret=modal.Secret.from_name("hearth-operations-secrets"),
# )
def driver():
    client = arxiv.Client()

    search_query = "agents OR llm OR stanford"

    search = arxiv.Search(
        query=search_query, max_results=10, sort_by=arxiv.SortCriterion.SubmittedDate
    )

    # `results` is a generator; you can iterate over its elements one by one...
    blocks = [create_slack_block("*📚 Daily Paper Crawl*\n\n")]
    cnt = 0
    for r in client.results(search):
        url = r.links[0].href
        paper_text = get_paper_text(url.replace("abs", "pdf"))
        # url = r.links[0].href.replace("abs", "html")
        # affiliations = get_possible_university_affiliations(url)
        if "cs" not in r.primary_category:
            continue
        # if not is_within_last_24_hours(str(r.published)):
        #     continue

        cnt += 1
        print(cnt)
        system_prompt = """You are receiving a computer science arxiv paper summary and its content. You are returning a python array with 3 entries, wrapped in [ ].
        1. Distill the summary into concise 1-2 lines.
        2. Return the author emails and their university affiliations from the paper content in a ; separated list. You can find this in the beginning before ABSTRACT.
        Eg: ashe@cs.stanford.edu, Stanford University; josh@harvard.dev, Harvard University
        3. Return keywords

        example output:
        ["The paper explores the challenges and techniques used by artificial agents in collectible card games like Hearthstone and Legends of Code and Magic, highlighting the limitations of current search methods due to vast state spaces and presenting analysis results of the ByteRL agent.", "collectible card games, Hearthstone, Legends of Code and Magic, artificial agents, imperfect information, ByteRL, state space, search methods", "Stanford University"]
        """

        output = call_openai(
            system_prompt=system_prompt,
            user_prompt=f"summary: {r.summary}\n\n paper content: {str(paper_text)}",
        )
        extracted_data = ast.literal_eval(output)
        slack_link = create_slack_link("🔗 Paper", r.pdf_url)
        stanford_included = (
            "🌲 Stanford" if "stanford" in extracted_data[1].lower() else ""
        )
        published = format_human_readable(str(r.published))

        blocks.append(
            create_slack_block(
                f"*{r.title}*\n_{published}_\n{extracted_data[0]}\n*Keywords: {extracted_data[2]}*\nAffiliations: {extracted_data[1]}\n{stanford_included}\n{slack_link}"
            )
        )
    slack_client.chat_postMessage(channel="<CHANNEL ID>", blocks=blocks)


if __name__ == "__main__":
    driver()
