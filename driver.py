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


@stub.function(
    schedule=modal.Cron("30 15 * * 1-5"),
    image=modal.Image.debian_slim().pip_install(
        [
            "slack-sdk",
            "python-dotenv",
            "requests",
            "emoji",
            "openai",
            "arxiv",
            "pytz",
            "bs4",
        ]
    ),
    secret=modal.Secret.from_name("hearth-operations-secrets"),
)
def driver():
    client = arxiv.Client()

    search_query = "agents OR llm OR stanford"

    search = arxiv.Search(
        query=search_query, max_results=30, sort_by=arxiv.SortCriterion.SubmittedDate
    )

    # `results` is a generator; you can iterate over its elements one by one...
    blocks = [create_slack_block("*📚 Daily Paper Crawl*\n\n")]
    cnt = 0
    for r in client.results(search):
        url = r.links[0].href
        url = r.links[0].href.replace("abs", "html")
        affiliations = get_possible_university_affiliations(url)
        if "cs" not in r.primary_category:
            continue
        if not is_within_last_24_hours(str(r.published)):
            continue

        cnt += 1
        print(cnt)
        system_prompt = """You are receiving a computer science arxiv paper summary and a list of links on the page. Distill the summary into concise 1-2 lines.
        Add a new line and then a line of Keywords: and a comma separated list of keywords. Wrap the keyword line in * to bold in slack. Only use * on each side.
        For example,

        *Keywords: cultural knowledge bases, CultureBank, language models, cultural awareness, TikTok, Reddit, scalable pipeline*
        """

        summary_processed = call_openai(
            system_prompt=system_prompt,
            user_prompt=f"summary: {r.summary}\n",
        )
        slack_link = create_slack_link("🔗 Paper", r.pdf_url)
        authors = [author.name for author in r.authors]
        stanford_included = (
            "🌲 Stanford affiliation\n"
            if "stanford" in ", ".join(affiliations).lower()
            else ""
        )
        published = format_human_readable(str(r.published))
        affiliations_str = (
            f"\nAffiliations: {', '.join(affiliations)}"
            if len(affiliations) > 0
            else ""
        )
        summary_processed = summary_processed.replace("**", "*")
        blocks.append(
            create_slack_block(
                f"*{r.title}*\n_{published}_\n{summary_processed}{affiliations_str}\n{stanford_included}{slack_link}"
            )
        )

    slack_client.chat_postMessage(channel="<PUT CHANNEL ID HERE>", blocks=blocks)


if __name__ == "__main__":
    driver()
