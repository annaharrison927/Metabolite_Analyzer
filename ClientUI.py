import streamlit as st
import pandas as pd
import numpy as np
import time

st.title('Metabolite Analyzer')

dataframe = None
uploaded_file = st.file_uploader("Choose a file")
if uploaded_file is not None:
    dataframe = pd.read_csv(uploaded_file)

if st.checkbox('Show metabolites'):
    st.subheader('Metabolites')
    st.write(dataframe)

st.text_input("Please enter keyword(s) you would like to add to the search query "
              "(e.g.: \"Maize\" or \"Human health\"):", key="keyword")
keyword = st.session_state.keyword

def show_progress():
    latest_iteration = st.empty()
    bar = st.progress(0)

    for i in range(100):
        # Update the progress bar with each iteration.
        latest_iteration.text(f'Progress: {i + 1}')
        bar.progress(i + 1)
        time.sleep(0.1)

if st.button('Click to start analysis'):
    show_progress()

