import requests
import xml.etree.ElementTree as ElementTree
import os
import time
import random
import pandas as pd
from typing import List, Tuple
from dotenv import load_dotenv
from requests import Response
from enum import Enum, auto
from markdown_pdf import MarkdownPdf, Section

# --- Configuration & Setup ---
NCBI_BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent"
HEADERS = {'Content-Type': 'application/json'}
MAX_ARTICLES = 100

load_dotenv()
NCBI_API_KEY = os.environ.get("NCBI_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

class Method(Enum):
    SEARCH = auto()
    FETCH = auto()

def print_compliance_notice():
    print("-" * 60)
    print("NCBI COMPLIANCE NOTICE")
    print("This tool uses NCBI E-utilities. By proceeding, you agree to")
    print("NCBI's Disclaimer and Copyright notice:")
    print("https://www.ncbi.nlm.nih.gov/About/disclaimer.html")
    print("-" * 60)
    print()

def exponential_backoff_request(url: str, method: str = "GET", payload: dict = None, max_retries: int = 5) -> (
        requests.Response | None):
    """
        Makes an HTTP request with exponential backoff for handling rate limits (429) and server errors.
    """
    for attempt in range(max_retries):
        try:
            if method == "GET":
                response = requests.get(url)
            else:
                response = requests.post(url, headers=HEADERS, json=payload)

            if response.status_code != 429 and response.status_code < 500:
                response.raise_for_status()
                return response

            if response.status_code == 429:
                print(f"RATE LIMIT HIT (429) on attempt {attempt + 1}. Retrying...")
            else:  # 5xx errors
                print(f"Server Error ({response.status_code}) on attempt {attempt + 1}. Retrying...")

            delay = (2 ** attempt) + random.uniform(0, 1)
            print(f"Waiting for {delay:.2f} seconds before retry.")
            time.sleep(delay)

        except requests.exceptions.RequestException as e:
            print(f"Request failed: {e}. Retrying...")
            delay = (2 ** attempt) + random.uniform(0, 1)
            time.sleep(delay)

    print(f"Failed to retrieve data after {max_retries} attempts.")
    return None

def get_metabolites() -> List[str]:
    metabolite_file = pd.read_csv("metabolite_list.csv")
    metabolite_df = pd.DataFrame(metabolite_file)
    return metabolite_df['Metabolites'].tolist()

def create_url(query_value: str, method: Method = Method.SEARCH) -> str:
    """
    Creates either an esearch or efetch url depending on method parameter.
    :param query_value: str
    :param method: str
    :return: str
    """
    api_key_param = f"&api_key={NCBI_API_KEY}"
    if method == Method.FETCH:
        return f"{NCBI_BASE_URL}efetch.fcgi?db=pubmed&id={query_value}&retmode=xml&rettype=abstract{api_key_param}"
    else:
        return (
            f"{NCBI_BASE_URL}esearch.fcgi?db=pubmed&term={query_value.replace(' ', '+')}"
            f"&retmode=json&retmax={MAX_ARTICLES}&sort=relevance{api_key_param}"
        )

def parse_xml(response: Response) -> str:
    """
    Extracts info from each article to create a string of combined abstracts
    :param response: Response
    :return: str
    """
    root = ElementTree.fromstring(response.text)
    combined_abstract_text = ""

    for article in root.findall('.//PubmedArticle'):
        pmid_el = article.find(".//PMID")
        article_pmid = pmid_el.text if pmid_el is not None else "UnknownPMID"

        title_el = article.find(".//ArticleTitle")
        title = title_el.text if title_el is not None else "No Title"

        author_el = article.find(".//Author/LastName")
        author = author_el.text if author_el is not None else "Unknown"

        abstract_text = ""
        abstract_elements = article.findall('.//AbstractText')
        for element in abstract_elements:
            if element.text:
                abstract_text += element.text + " "

        if abstract_text:
            combined_abstract_text += f"\n\n--- ARTICLE PMID {article_pmid} | AUTHOR: {author}\nTITLE: {title}---\n{abstract_text.strip()}"

    return combined_abstract_text


def fetch_abstracts(search_term: str) -> Tuple[str, List[str]] | None:
    """
    Executes ESearch and EFetch to retrieve abstracts for the top MAX_ARTICLES results.
    Returns a single concatenated abstract string and a list of PMIDs.
    """
    esearch_url = create_url(search_term, Method.SEARCH)
    response = exponential_backoff_request(esearch_url)
    if not response:
        return None

    search_data = response.json()
    pmids = search_data['esearchresult']['idlist']
    if not pmids:
        return None

    pmid_list_str = ",".join(pmids)
    efetch_url = create_url(pmid_list_str, Method.FETCH)
    response = exponential_backoff_request(efetch_url)
    if not response:
        return None

    combined_abstract_text = parse_xml(response)
    return combined_abstract_text.strip(), pmids


def analyze_abstract(combined_abstract: str, analysis_task: str) -> str:
    """
    Sends the combined abstract content to the Gemini API for a single, consolidated analysis.
    """

    system_prompt = (
        "You are a concise, biomedical expert. Your summary must be objective, "
        "and synthesize the main findings from ALL provided abstracts into a single, cohesive, bulleted list. Each"
        "bullet point should be short and to the point. CITATION RULE: You MUST cite every claim using the provided SOURCE_ID (PMID)."
        "Format citations as [PMID: 1234567]. Following the citation, provide a link to the article in a sub-bullet as:"
        "   *(https://pubmed.ncbi.nlm.nih.gov/1234567/)"
        "Before your summary, create a subheading with the metabolite name (e.g.: \n## [Metabolite Name]\n)"
        "Each bullet should be under one of the following three subheadings: ###Positive Effects, ###Negative Effects, "
        "or ### Neutral/Context-Dependent Effects. Follow this template as a guide for formatting:"
        "\n## [Metabolite Name]\n"
        "\n### Positive Effects\n"
        "* **[Affected System]**: [Observation] [[000000; 111111]].\n"
        "    * (https://pubmed.ncbi.nlm.nih.gov/000000/)\n"
        "    * (https://pubmed.ncbi.nlm.nih.gov/111111/)\n"
        "\n### Negative Effects\n"
        "\n### Neutral/Context-Dependent Effects\n"
        "Do not repeat affected systems within your subcategories. For example, if you find multiple positive effects "
        "on the Cardiovascular system, list all effects in one bullet. Limit bullet size to 30 words, not including"
        "citations. Put two new line characters at the end of your summary."
    )

    full_query = (
        f"Consolidate and address the following analysis task based on the multiple provided abstracts: {analysis_task}"
        f"\n\nCOMBINED ABSTRACT CONTENT:\n---\n{combined_abstract}"
    )

    payload = {
        "contents": [{"parts": [{"text": full_query}]}],
        "systemInstruction": {"parts": [{"text": system_prompt}]}
    }

    gemini_url_with_key = f"{GEMINI_API_URL}?key={GEMINI_API_KEY}"

    response = exponential_backoff_request(gemini_url_with_key, method="POST", payload=payload)

    if response:
        response_data = response.json()
        return response_data['candidates'][0]['content']['parts'][0]['text']
    else:
        return "An error occurred during the Gemini API call after multiple retries."

def process_metabolite(metabolite, task):
    """
    Orchestrates the full flow for a single metabolite.
    """
    try:
        combined_abstract, pmids = fetch_abstracts(metabolite)
        if not combined_abstract:
            return f"No abstracts found for {metabolite}."

        report = analyze_abstract(combined_abstract, task)
        return report

    except Exception as e:
        return f"Error processing {metabolite}: {str(e)}"

def write_report(report: str, file_name: str):
    with open(f"{file_name}.md", "w", encoding="utf-8") as md_file:
        md_file.write(report)
    with open(f"{file_name}.md", "r", encoding="utf-8") as md_file:
        markdown_content = md_file.read()

    pdf = MarkdownPdf()
    pdf.add_section(Section(markdown_content))
    pdf.save(f"{file_name}.pdf")

def main():
    print_compliance_notice()
    metabolites_to_process = get_metabolites()
    analysis_task = "positive, negative, and neutral effects of metabolite on human body systems"
    all_reports = {}
    report_string = ""
    start_time = time.time()

    for metabolite in metabolites_to_process:
        print(f"\n--- Starting Analysis: {metabolite} ---\n")
        report = process_metabolite(metabolite, analysis_task)
        all_reports[metabolite] = report
        report_string = report_string + report
        print(f"  > Result: {report}\n")
        print("15-second server cooldown...\n")
        time.sleep(15)

    final_report = "# Metabolite Report\n" + report_string
    file_name = "metabolite_report4"
    write_report(final_report, file_name)

    end_time = time.time()

    print("\n" + "=" * 60)
    print("BATCH PROCESSING COMPLETE")
    print(f"Total time: {round(end_time - start_time, 2)} seconds")
    print(f"Metabolites processed: {len(metabolites_to_process)}")
    print("=" * 60)

if __name__ == "__main__":
    if not NCBI_API_KEY or not GEMINI_API_KEY:
        print("ERROR: API keys not found. Check your .env file.")
    else:
        main()
