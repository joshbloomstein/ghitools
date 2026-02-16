#!/usr/bin/env python
# coding: utf-8

# In[18]:


#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

import re
import requests
import sys

from datetime import datetime
from collections import Counter
from io import StringIO
from urllib.request import urlretrieve
from urllib.parse import quote
from shiny import App, reactive, render, ui

pd.set_option('display.max_rows', None)

# In[ ]:


def generate_url(startdate, enddate, passkey):
    urlstem = 'https://www.amion.com/cgi-bin/ocs?Lo={}&Rpt=625ctabs'.format(
        passkey
    )

    y, m, d = startdate.strftime('%y'), startdate.month, startdate.day
    delta = (enddate - startdate).days
    datestring = '&Day={}&Month={}-{}&Days={}'.format(d, m, y, delta)

    return urlstem + datestring

def fetch_table(url):
    req = Request(
        url,
        headers = {
            'User-Agent': (
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/120.0.0.0 Safari/537.36'
            ),
            'Accept': 'text/plain, */*;q=0.9',
            'Connection': 'keep-alive',
        },
    )

    with urlopen(req, timeout = 60) as resp:
        text = resp.read().decode('utf-8', errors = 'replace')

    return StringIO(text)

def download_df(academicYear, passkey):
    if academicYear == 'AY23':
        startdate = datetime(2023, 6, 28)
        enddate = datetime(2024, 6, 30)
    elif academicYear == 'AY24':
        startdate = datetime(2024, 7, 1)
        enddate = datetime(2025, 6, 29)
    elif academicYear == 'AY25':
        startdate = datetime(2025, 6, 30)
        enddate = datetime(2026, 6, 29)
    else:
        startdate = datetime(1, 1, 1)
        enddate = datetime(1, 1, 2)

    passkey_encoded = quote(passkey)
    url = generate_url(startdate, enddate, passkey_encoded)
    file_like = fetch_table(url)

    try:
        df = pd.read_table(
            file_like,
            skiprows = 7,
            header = None,
            usecols = [0, 3, 6, 7, 8, 9, 15, 16],
        )
    except pd.errors.EmptyDataError:
        return pd.DataFrame([])

    df.columns = [
        'Name', 'Assignment', 'Date', 'Start', 'Stop', 'Role', 'Type', 'Assgn'
    ]

    df = df[~df.Role.isnull()]
    df = df[df.Role != 'Services']
    df = df[df.Role.str[-1] != '*']

    df['Assignment'] = (
        df['Assignment']
        .astype(str)
        .str.strip()
        .str.replace(r'\s+', ' ', regex = True)
    )

    return df

def download_df_multi_year(academicYears, passkey):
    dfs = []
    for ay in academicYears:
        dfi = download_df(ay, passkey)
        if not dfi.empty:
            dfs.append(dfi)

    if not dfs:
        return pd.DataFrame([])

    return pd.concat(dfs, ignore_index = True)


app_ui = ui.page_fluid(
    ui.h3('Amion Rotation Openings Checker'),
    ui.layout_sidebar(
        ui.sidebar(
            ui.input_password('passkey_ui', 'Amion passkey (optional override)'),
            ui.input_select(
                'years',
                'Academic years',
                choices = ['AY23', 'AY24', 'AY25'],
                selected = ['AY23', 'AY24', 'AY25'],
                multiple = True,
            ),
            ui.input_text('month', 'Month (YYYY-MM)', value = '2026-02'),
            ui.input_action_button('load', 'Load / Refresh'),
            width = 4,
        ),
        ui.output_text_verbatim('status'),
    )
)

def server(input, output, session):
    status_state = reactive.Value(
        'Click Load / Refresh. Passkey is read from AMION_PASSKEY unless you '
        'enter an override.'
    )

    @reactive.Effect
    @reactive.event(input.load)
    def _load():
        years = input.years()
        month = (input.month() or '').strip()

        passkey_env = (os.getenv('AMION_PASSKEY') or '').strip()
        passkey_ui = (input.passkey_ui() or '').strip()
        passkey = passkey_ui if passkey_ui != '' else passkey_env

        if passkey == '':
            status_state.set(
                'No passkey available. Set AMION_PASSKEY on the server, or '
                'enter it in the UI.'
            )
            return

        if not years:
            status_state.set('No years selected.')
            return

        if not re.match(r'^\d{4}-\d{2}$', month):
            status_state.set('Month must be YYYY-MM (example: 2026-02).')
            return

        try:
            df = download_df_multi_year(list(years), passkey)
            status_state.set('Loaded rows = {}'.format(len(df)))
        except Exception as e:
            status_state.set('Error: {}'.format(e))

    @output
    @render.text
    def status():
        return status_state.get()

app = App(app_ui, server)

