import requests
import xml.etree.ElementTree as ElementTree
import os
import time
import random
import pandas as pd
import tempfile
from typing import List, Tuple
from dotenv import load_dotenv
from requests import Response
from enum import Enum, auto
from markdown_pdf import MarkdownPdf, Section
from MyAI import MyAI

# --- Configuration & Setup ---
NCBI_BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
HEADERS = {'Content-Type': 'application/json'}
MAX_ARTICLES = 100

load_dotenv()
NCBI_API_KEY = os.environ.get("NCBI_API_KEY")

model_name = "gemini-flash-lite-latest"

system_prompt = (
    "You are a biomedical expert specializing in metabolite research. "
    "Your summary must be objective and synthesize the main findings from ALL provided abstracts. "
    "\n\n### MANDATORY FORMATTING RULE ###\n"
    "1. Start with: ## [Metabolite Name]\n"
    "2. Use subheadings: ### Positive Effects, ### Negative Effects, ### Neutral/Context-Dependent Effects.\n"
    "3. Every claim MUST include a citation: [PMID: 1234567].\n"
    "4. IMMEDIATELY after every bullet, you MUST provide the full PubMed URL in a sub-bullet. "
    "Example:\n"
    "* Observation text [PMID: 000000]\n"
    "    * https://pubmed.ncbi.nlm.nih.gov/000000/\n"
    "\n### CONSTRAINTS ###\n"
    "- NEVER omit a URL. Each citation needs its own dedicated sub-bullet URL.\n"
    "- Each main bullet should be approximately 30 words of text (citations and sub-bullet URLs do NOT count toward this limit).\n"
    "- Do not repeat body systems within a single subheading category.\n"
    "- Be concise and factual; avoid introductory fluff."
)

my_ai = MyAI(model_name=model_name,system_prompt=system_prompt)

class Method(Enum):
    SEARCH = auto()
    FETCH = auto()

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
    metabolite_file = pd.read_csv("metabolite_list_small.csv")
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
    seen_titles = set()

    for article in root.findall('.//PubmedArticle'):
        pmid_el = article.find(".//PMID")
        article_pmid = pmid_el.text if pmid_el is not None else "UnknownPMID"

        title_el = article.find(".//ArticleTitle")
        title = title_el.text if title_el is not None else "No Title"

        if title in seen_titles:
            continue

        seen_titles.add(title)

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

def analyze_abstract(combined_abstracts: str, task: str) -> str:
    input_text = task + combined_abstracts
    return my_ai.generate_response(input_text=input_text)

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

def get_pdf_bytes(report_text: str):
    """Generates a PDF from markdown and returns the raw bytes."""
    pdf = MarkdownPdf()
    pdf.add_section(Section(report_text))

    # Permission fix: Use a manual path to ensure file is closed
    fd, temp_path = tempfile.mkstemp(suffix=".pdf")
    try:
        os.close(fd)  # Close the file descriptor immediately
        pdf.save(temp_path)  # Let the library write to the closed path

        with open(temp_path, "rb") as f:
            pdf_bytes = f.read()

        return pdf_bytes
    finally:
        # Now it is safe to remove because no process is holding the file
        if os.path.exists(temp_path):
            os.remove(temp_path)

def run_analysis(metabolite:str, keyword: str):
    if not NCBI_API_KEY:
        return "ERROR: API keys not found. Check your .env file."
    else:
        analysis_task = (f"Report positive, negative, and neutral effects of the following metabolite on {keyword}: "
                         f"{metabolite}. Write your report based upon format in your system instructions "
                         f"and the following abstracts:")
        report_string = ""
        start_time = time.time()

        report = process_metabolite(metabolite, analysis_task)
        report_string = report_string + report + "\n"
        time.sleep(15)

        end_time = time.time()

        return report_string

def main(metabolite: str, keyword: str):
    return run_analysis(metabolite, keyword)

if __name__ == "__main__":
    pass
