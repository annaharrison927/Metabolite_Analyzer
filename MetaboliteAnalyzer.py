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
AMINO_ACIDS = {'Ala', 'Arg', 'Asn', 'Asp', 'Cys',
               'Glu', 'Gln', 'Gly', 'His', 'Ile',
               'Leu', 'Lys', 'Met', 'Phe', 'Pro',
               'Ser', 'Thr', 'Trp', 'Tyr', 'Val'}

load_dotenv()
NCBI_API_KEY = os.environ.get("NCBI_API_KEY")

model_name = "gemini-flash-lite-latest"

system_prompt = (
    "You are a biomedical literature assistant specializing in metabolite research. "
    "Synthesize the provided PubMed abstracts into a concise, objective summary of the "
    "biological roles or findings they support. Treat the abstracts as the evidence base; "
    "do not present a reported association as proof of causation or a finding in one species "
    "as established in all species.\n\n"
    "### REQUIRED FORMAT ###\n"
    "1. Start with a level-two Markdown heading containing the metabolite's name, without "
    "brackets (for example, ## Citrate).\n"
    "2. Follow with one paragraph of no more than three sentences.\n"
    "3. Cover the main supported role or finding, relevant biological context, and the "
    "strength or limitations of the evidence when the abstracts support those points.\n"
    "4. Include PMID citations in the paragraph for factual claims, formatted as "
    "[PMID: 1234567](https://pubmed.ncbi.nlm.nih.gov/1234567/). The PMID in the link "
    "text and URL must match. Cite only the most relevant supporting abstracts; do not "
    "add separate URL bullets.\n\n"
    "### EVIDENCE AND STYLE RULES ###\n"
    "- Synthesize the main themes across the provided abstracts rather than listing "
    "each abstract or finding individually.\n"
    "- If a search focus or keywords are provided, prioritize relevant findings only "
    "when supported by the abstracts. If the abstracts do not address the focus, say so "
    "briefly; do not infer the requested property from the search terms alone.\n"
    "- Cite only PMIDs included in the supplied abstracts. Never invent or guess a PMID.\n"
    "- Use fewer than three sentences if the evidence is sparse; do not invent details "
    "to fill the format.\n"
    "- If findings are mixed or context-dependent, say so briefly and identify the "
    "relevant context when available.\n"
    "- If the abstracts do not support a clear biological role or finding, state that "
    "the available abstracts are insufficient to determine one.\n"
    "- Do not infer that a metabolite has no effect merely because the provided abstracts "
    "do not report one.\n"
    "- Be concise and factual. Omit introductory or closing commentary."
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
    last_exception = None
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
            last_exception = e
            print(f"Request failed: {e}. Retrying...")
            delay = (2 ** attempt) + random.uniform(0, 1)
            time.sleep(delay)


    raise RuntimeError(f"Failed to retrieve data after {max_retries} attempts.")

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

def is_peptide(metabolite: str) -> bool:
    return all(part in AMINO_ACIDS
               for part in metabolite.split("-"))

def normalize_peptide_notation(abstract:str) -> str:
    normalized = abstract
    normalized = normalized.replace("·", "-")
    normalized = normalized.replace("–", "-")
    normalized = normalized.replace("—", "-")
    normalized = normalized.replace("−", "-")
    return normalized

# Check if abstracts contain peptide of interest
def is_valid_abstract(abstract: str, peptide: str) -> bool:
    parts = abstract.split(peptide)
    beg = parts[0]
    end = parts[-1]

    if len(parts) == 2:
        if (beg == "" or beg[-1]) != "-" and (end == "" or end[0] != "-"):
            return True
        return False
    else:
        for i in range(len(parts)-1):
            front = parts[i]
            back = parts[i+1]
            if front[-1] != "-" and back[0] != "-":
                return True
        return False

def build_abstract_corpus(response: Response, metabolite:str) -> str:
    """
    Extracts info from each article to create a string of combined abstracts
    :param metabolite: str
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
        peptide = is_peptide(metabolite)
        for element in abstract_elements:
            if element.text:
                abstract_text += element.text + " "
        if peptide:
            normalized_abstract = normalize_peptide_notation(abstract_text)
            if not is_valid_abstract(normalized_abstract, metabolite):
                continue
        if abstract_text:
            combined_abstract_text += f"\n\n--- ARTICLE PMID {article_pmid} | AUTHOR: {author}\nTITLE: {title}---\n{abstract_text.strip()}"

    return combined_abstract_text

def fetch_abstracts(metabolite: str) -> Tuple[str, List[str]] | None:
    """
    Executes ESearch and EFetch to retrieve abstracts for the top MAX_ARTICLES results.
    Returns a single concatenated abstract string and a list of PMIDs.
    """
    esearch_url = create_url(metabolite, Method.SEARCH)
    response = exponential_backoff_request(esearch_url)
    if not response:
        return "", []

    search_data = response.json()
    pmids = search_data['esearchresult']['idlist']
    if not pmids:
        return "", []

    pmid_list_str = ",".join(pmids)
    efetch_url = create_url(pmid_list_str, Method.FETCH)
    response = exponential_backoff_request(efetch_url)
    if not response:
        return "", []

    combined_abstract_text = build_abstract_corpus(response, metabolite)
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
            return (f"\n## {metabolite}\n"
                    f"No PubMed publications were found for the metabolite \"{metabolite}\" using the current search "
                    f"strategy. This does not necessarily indicate that the metabolite has no biological effects; only "
                    f"that no matching publications were retrieved from PubMed.\n")
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

def analyze_metabolite(metabolite:str, keyword: str):
    if not NCBI_API_KEY:
        return "ERROR: API keys not found. Check your .env file."
    else:
        analysis_task = (
                f"Summarize the biological roles or findings supported by the provided PubMed "
                f"abstracts for the metabolite {metabolite}."
                + (f" Focus on this search topic: {keyword}." if keyword and keyword.strip() else "")
                + " Follow the required format and evidence rules in your system instructions. "
                  "Use only the provided abstracts as evidence."
        )
        report_string = ""
        report = process_metabolite(metabolite, analysis_task)
        report_string = report_string + report + "\n"
        # time.sleep(15)
        return report_string

def main(metabolite: str, keyword: str):
    return analyze_metabolite(metabolite, keyword)

if __name__ == "__main__":
    pass
