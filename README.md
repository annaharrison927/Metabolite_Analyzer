# PubMed Metabolite Analyzer (NCBI & OpenAI)

This Streamlit application searches PubMed through NCBI E-utilities, retrieves relevant abstracts for metabolites, and uses the OpenAI Responses API to produce a concise, evidence-based summary. Optional comma- or semicolon-separated keywords can focus both the PubMed search and the model summary.

The default model is `gpt-6-luna`. The model and API key are configurable through environment variables.

## Setup

Create and activate a Python virtual environment, then install the dependencies:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Create a `.env` file in the project root. Add your own keys locally; never commit this file:

```text
OPENAI_API_KEY=your_openai_api_key
NCBI_API_KEY=your_ncbi_api_key
OPENAI_MODEL=gpt-6-luna
```

`OPENAI_MODEL` is optional and defaults to `gpt-6-luna`. The application requires both API keys to run analysis. Keep `.env` excluded from version control.

## Run the app

```powershell
streamlit run ClientUI.py
```

Upload a CSV file whose first column is named `Metabolites`, enter optional search keywords, agree to the NCBI terms, and start the analysis. Each metabolite is searched with the metabolite phrase AND the supplied keyword alternatives, with terms restricted to PubMed title and abstract fields.

## Results and downloads

The app returns one concise sentence per metabolite and offers PDF, CSV, and Excel workbook downloads:

- The PDF formats validated PMID citations as clickable PubMed links.
- The CSV contains one row per metabolite with `Metabolite`, `Summary`, `Status`, and `PMIDs` columns. PMID values are plain text.
- The Excel workbook contains a `Summaries` sheet and a `References` sheet with clickable PubMed links.

Statuses distinguish `complete` (a relevant function was summarized), `no_relevant_results` (abstracts were retrieved but did not support a function relevant to the focus), `no_results` (no abstracts were retrieved), and `error` (processing failed). Citations are checked against PMIDs in the retrieved abstract corpus before they are linked or exported.

After analysis, the app displays input, output, and total token counts and an estimated API cost separately from the downloadable report. The estimate uses the configured model's standard, uncached input/output rates. The `gpt-6-luna` rates in the application were checked on 2026-11-16 and are stored with an effective date in `MetaboliteAnalyzer.py`; rates may change. Prompt caching, account-specific pricing, and billing adjustments can make the estimate differ from the billed amount. Unknown token usage or an unconfigured model rate is shown as unavailable.

The OpenAI request uses structured JSON output. It does not set a temperature; the model/API default applies. The request uses the system instructions and schema to enforce the status and summary fields. NCBI retrieval and its rate-limiting behavior remain separate from model summarization.

## Configuration and troubleshooting

- If analysis reports a missing OpenAI key, set `OPENAI_API_KEY` in `.env` and restart Streamlit.
- If NCBI search fails because its key is missing, set `NCBI_API_KEY` in `.env` and restart Streamlit. NCBI key usage is subject to NCBI's E-utilities policies.
- If you change `OPENAI_MODEL`, confirm that the model is available to your API account and supports structured outputs. Cost estimates are available only for models with a configured rate entry.
- Install dependencies in the same virtual environment used to run Streamlit with `python -m pip install -r requirements.txt`.

This project uses NCBI E-utilities. By using it, you agree to NCBI's [Disclaimer and Copyright notice](https://www.ncbi.nlm.nih.gov/About/disclaimer.html).
