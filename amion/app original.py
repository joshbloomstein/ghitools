#!/usr/bin/env python
# coding: utf-8

from __future__ import annotations

import pandas as pd
import re
import requests

from datetime import datetime
from io import StringIO
from urllib.parse import quote
from shiny import App, reactive, render, ui

def generate_url(startdate, enddate, passkey):
    urlstem = f'https://www.amion.com/cgi-bin/ocs?Lo={passkey}&Rpt=625ctabs'
    y, m, d = startdate.strftime('%y'), startdate.month, startdate.day
    delta = (enddate - startdate).days
    return f'{urlstem}&Day={d}&Month={m}-{y}&Days={delta}'

def fetch_table(url):
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/plain",
        "Connection": "close",
    }

    try:
        r = requests.get(url, headers=headers, timeout=60)
        r.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"REQUEST FAILED: {e}")

    text = r.text

    if "<html" in text.lower():
        raise RuntimeError("Received HTML instead of table (invalid Access Code or blocked)")

    if len(text.strip()) < 100:
        raise RuntimeError("Response too short (likely blocked or bad Access Code)")

    return StringIO(text)

def _make_exclude_regex():
    banned_terms = [
        'Conf', 'Didactic', 'Exam', 'Panel', 'Retreat', 'R1', 'R2', 'R3',
        'SOM Resc', 'Resc', 'ABIM', 'Board Prep',
        'Chief', 'Clinic', 'Holiday', 'Off', 'Immersion', 'Academic',
        'Vacation', 'Sick', 'Interview', 'PPC', 'Shadow', 'TBD', 'Jury',
        'ACGME', 'ACLS', 'BELL Outpatient', 'H Med', 'Immersion', 'PC', 'RaTL',
        'Panel Handoff', 'Risk', 'Bereavement', 'QI Project', 'Graduated',
        'Health Equity', 'H ER', 'H MICU', 'Just-in-Time', 'Orientation', 'U ER',
        'Vac', 'nan', 'H Neuro', 'U Med', 'V Med', 'V Night Med', 'V MICU',
        'V Cards', 'U Cards A', 'Elective', 'U HO', 'LWOP', 'H Geri', 'H CCU'
        'H Cards Consult', 'GIM', 'GME', 'Precept', 'Stud Eve H', 'Swing',
        'Primary Care'
    ]
    pattern = r'(?:' + r'|'.join(re.escape(t) for t in banned_terms) + r')'
    return re.compile(pattern, flags=re.IGNORECASE)

_EXCLUDE_RE = _make_exclude_regex()

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
    elif academicYear == 'AY26':
        startdate = datetime(2026, 6, 30)
        enddate = datetime(2027, 6, 29)
    else:
        return pd.DataFrame([])

    url = generate_url(startdate, enddate, quote(passkey))
    file_like = fetch_table(url)

    try:
        text = file_like.getvalue()
        lines = text.splitlines()
        data_lines = [l for l in lines if "\t" in l and "," in l]

        if len(data_lines) == 0:
            raise RuntimeError("No valid data lines found")

        df_raw = pd.read_csv(
            StringIO("\n".join(data_lines)),
            sep="\t",
            header=None,
            engine="python"
        )

        cols = df_raw.shape[1]

        df = pd.DataFrame({
            'Name': df_raw.iloc[:, 0],
            'Assignment': df_raw.iloc[:, 3] if cols > 3 else '',
            'Date': df_raw.iloc[:, 6] if cols > 6 else '',
            'Start': df_raw.iloc[:, 7] if cols > 7 else '',
            'Stop': df_raw.iloc[:, 8] if cols > 8 else '',
            'Role': df_raw.iloc[:, 9] if cols > 9 else '',
            'Type': df_raw.iloc[:, 15] if cols > 15 else '',
            'Assgn': df_raw.iloc[:, 16] if cols > 16 else '',
        })

    except Exception:
        return pd.DataFrame([])

    df.columns = [
        'Name','Assignment','Date','Start','Stop','Role','Type','Assgn'
    ]

    df = df[~df.Role.isnull()]
    df = df[df.Role != 'Services']
    df = df[df.Role.astype(str).str[-1] != '*']

    df['Name'] = df['Name'].astype(str).str.replace("'", '').str.strip()
    df['Assignment'] = (
        df['Assignment']
        .astype(str)
        .str.strip()
        .str.replace(r',\s*(am|pm)\s*$', '', regex=True, flags=re.IGNORECASE)
        .str.replace(r'\s+', ' ', regex=True)
    )

    df = df[df['Assignment'].notna()]
    df = df[df['Assignment'] != '']
    df = df[~df['Assignment'].str.contains(_EXCLUDE_RE, na=False)]

    return df

def download_df_multi_year(years, passkey):
    dfs = []
    for y in years:
        d = download_df(y, passkey)
        if not d.empty:
            dfs.append(d)
    return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame([])

def build_master_rotations(df):
    return sorted(df['Assignment'].dropna().unique().tolist())

def rotations_unfilled_in_month(df, master, month):
    start = pd.to_datetime(month + '-01')
    end = start + pd.offsets.MonthBegin(1)

    df_month = df.loc[start:end]

    filled = set(df_month['Assignment'])
    return sorted(set(master) - filled)

app_ui = ui.page_fluid(

    ui.h3('Amion Rotation Openings Checker'),

    ui.input_text('passkey', 'Amion Access Code'),

    ui.layout_sidebar(
        ui.sidebar(
            ui.input_select(
                'years',
                'Academic years',
                ['AY23','AY24','AY25','AY26'],
                selected=['AY23','AY24','AY25','AY26'],
                multiple=True
            ),
            ui.input_text('month','Month (YYYY-MM)','2026-02'),
            ui.input_action_button('load','Load / Refresh data'),
            ui.input_action_button('check','Check month')
        ),
        ui.div(
            ui.output_text_verbatim('status'),
            ui.hr(),
            ui.h4('All assignments (count)'),
            ui.output_text('master_count'),
            ui.h4('Assignments that may have openings'),
            ui.output_table('unfilled_table'),
        )
    )
)

def server(input, output, session):

    df_state = reactive.Value(pd.DataFrame([]))
    master_state = reactive.Value([])
    status_state = reactive.Value('Ready')
    unfilled_state = reactive.Value(pd.DataFrame([]))

    @reactive.Effect
    @reactive.event(input.load)
    def load_data():

        pk = (input.passkey() or '').strip()
        years = list(input.years() or [])

        if pk == '':
            status_state.set('No Access Code entered.')
            return

        try:
            status_state.set('Loading...')

            df = download_df_multi_year(years, pk)

            if df.empty:
                status_state.set('No data returned.')
                return

            df['Date'] = df['Date'].astype(str).str.strip()

            df['Date_dt'] = pd.to_datetime(
                df['Date'],
                format='%m-%d-%y',
                errors='coerce'
            )

            df = df[df['Date_dt'].notna()]

            df = df.sort_values('Date_dt')

            df = df.set_index('Date_dt')

            master = build_master_rotations(df)

            df_state.set(df)
            master_state.set(master)

            status_state.set(
                f'Loaded rows = {len(df)}, all assignments = {len(master)}'
            )

        except Exception as e:
            status_state.set(f'Load failed: {e}')

    @reactive.Effect
    @reactive.event(input.check)
    def check_month():

        df = df_state.get()
        master = master_state.get()
        month = (input.month() or '').strip()

        if df.empty:
            status_state.set('Load data first.')
            return

        if not re.match(r'^\d{4}-\d{2}$', month):
            status_state.set('Invalid month format.')
            return

        result = rotations_unfilled_in_month(df, master, month)

        unfilled_state.set(pd.DataFrame({'Assignment': result}))
        status_state.set(f'{len(result)} openings found')

    @output
    @render.text
    def status():
        return status_state.get()

    @output
    @render.text
    def master_count():
        return str(len(master_state.get()))

    @output
    @render.table
    def unfilled_table():
        return unfilled_state.get()

app = App(app_ui, server)