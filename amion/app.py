#!/usr/bin/env python
# coding: utf-8

from __future__ import annotations

import pandas as pd
import re
import requests
import sys

from datetime import datetime
from collections import Counter
from io import StringIO
from urllib.request import urlretrieve, Request, urlopen
from urllib.parse import quote
from shiny import App, reactive, render, ui

def generate_url(startdate, enddate, passkey):
    urlstem = 'https://www.amion.com/cgi-bin/ocs?Lo={}&Rpt=625ctabs'.format(
        passkey
    )

    y, m, d = startdate.strftime('%y'), startdate.month, startdate.day
    delta = (enddate - startdate).days
    datestring = '&Day={}&Month={}-{}&Days={}'.format(d, m, y, delta)

    return urlstem + datestring

def fetch_table(url):
    headers = {
        'User-Agent': (
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/120.0.0.0 Safari/537.36'
        ),
        'Accept': 'text/plain, */*;q=0.9',
        'Connection': 'keep-alive',
    }

    req = Request(url, headers = headers)
    with urlopen(req, timeout = 60) as resp:
        text = resp.read().decode('utf-8', errors = 'replace')

    return StringIO(text)

def download_df(academicYear, passkey):
    if academicYear == 'AY22':
        startdate = datetime(2022, 6, 24)
        enddate = datetime(2023, 6, 27)
    elif academicYear == 'AY23':
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
        'Name',
        'Assignment',
        'Date',
        'Start',
        'Stop',
        'Role',
        'Type',
        'Assgn',
    ]

    df = df[~df.Role.isnull()]
    df = df[df.Role != 'Services']
    df = df[df.Role.str[-1] != '*']

    df['Name'] = (
        df['Name']
        .astype(str)
        .str.replace("'", '', regex = False)
        .str.replace('"', '', regex = False)
        .str.strip()
    )

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
            dfi['AcademicYear'] = ay
            dfs.append(dfi)

    if not dfs:
        return pd.DataFrame([])

    return pd.concat(dfs, ignore_index = True)

def _parse_date_column(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df['Date_dt'] = pd.to_datetime(
        df['Date'],
        errors = 'coerce',
        infer_datetime_format = True
    )
    return df

def _clean_rotation_text(s: str) -> str:
    s = str(s).strip()
    s = re.sub(r'\s+', ' ', s)
    s = re.sub(r',\s*(am|pm)\s*$', '', s, flags = re.IGNORECASE)
    return s

def _make_exclude_regex():
    banned_terms = [
        'Conf', 'Didactic', 'Exam', 'Panel', 'Retreat', 'R1', 'R2', 'R3',
        'SOM Resc', 'Resc', 'ABIM', 'Board Prep',
        'Chief', 'Clinic', 'Holiday', 'Off', 'Immersion', 'Academic',
        'Vacation', 'Sick', 'Interview', 'PPC', 'Shadow', 'TBD', 'Jury',
        'ACGME'
    ]
    pattern = r'(' + r'|'.join(re.escape(t) for t in banned_terms) + r')'
    return re.compile(pattern, flags = re.IGNORECASE)

_EXCLUDE_RE = _make_exclude_regex()

def _prepare_rotations_df(df: pd.DataFrame) -> pd.DataFrame:
    df2 = df.copy()
    df2 = _parse_date_column(df2)

    df2['Rotation'] = df2['Assignment'].map(_clean_rotation_text)

    df2 = df2[df2['Rotation'].notna()]
    df2 = df2[df2['Rotation'].astype(str).str.strip() != '']
    df2 = df2[~df2['Rotation'].str.contains(_EXCLUDE_RE, na = False)]

    df2 = df2[df2['Name'].notna()]
    df2 = df2[df2['Name'].astype(str).str.strip() != '']

    df2 = df2[df2['Date_dt'].notna()]

    return df2[['Name', 'Rotation', 'Date_dt']].copy()

def _rotations_with_repeat_use(df_rot: pd.DataFrame, min_count = 6, window_days = 92) -> set:
    window_ns = int(window_days * 24 * 60 * 60 * 1_000_000_000)
    qualifying = set()

    df_rot = df_rot.copy()
    df_rot['t'] = df_rot['Date_dt'].values.astype('datetime64[ns]').astype('int64')
    df_rot = df_rot.sort_values(['Name', 'Rotation', 't'])

    for (name, rot), g in df_rot.groupby(['Name', 'Rotation'], sort = False):
        t = g['t'].to_numpy()
        i = 0
        for j in range(len(t)):
            while t[j] - t[i] > window_ns:
                i += 1
            if (j - i + 1) >= min_count:
                qualifying.add(rot)
                break

    return qualifying

def build_master_rotations(df: pd.DataFrame) -> list[str]:
    df_rot = _prepare_rotations_df(df)
    qualifying = _rotations_with_repeat_use(df_rot = df_rot, min_count = 6, window_days = 92)
    return sorted(qualifying, key = lambda x: x.lower())

def rotations_unfilled_in_month(df: pd.DataFrame, master_rotations: list[str], month_yyyy_mm: str) -> list[str]:
    df_rot = _prepare_rotations_df(df)

    month_start = pd.to_datetime(month_yyyy_mm + '-01')
    month_end = month_start + pd.offsets.MonthBegin(1)

    in_month = df_rot[(df_rot['Date_dt'] >= month_start) & (df_rot['Date_dt'] < month_end)]
    filled = set(in_month['Rotation'].dropna().unique().tolist())

    unfilled = [r for r in master_rotations if r not in filled]
    unfilled = sorted(set(unfilled), key = lambda x: x.lower())
    unfilled = [r.replace('*', '') for r in unfilled]
    return unfilled

app_ui = ui.page_fluid(
    ui.h3('Amion Rotation Openings Checker'),
    ui.layout_sidebar(
        ui.sidebar(
            ui.input_password('passkey', 'Amion passkey (hidden)'),
            ui.input_select(
                'years',
                'Academic years to scan (master list)',
                choices = ['AY23', 'AY24', 'AY25'],
                selected = ['AY23', 'AY24', 'AY25'],
                multiple = True,
            ),
            ui.input_text('month', 'Month to check (YYYY-MM)', value = '2026-02'),
            ui.input_action_button('load', 'Load / Refresh data'),
            ui.input_action_button('check', 'Check month'),
            width = 4
        ),
        ui.div(
            ui.output_text_verbatim('status'),
            ui.hr(),
            ui.h4('Master rotations (count)'),
            ui.output_text('master_count'),
            ui.h4('Rotations that may have openings'),
            ui.output_table('unfilled_table'),
            ui.h4('Raw list'),
            ui.output_text_verbatim('unfilled_list'),
        )
    )
)

def server(input, output, session):
    df_state = reactive.Value(pd.DataFrame([]))
    master_state = reactive.Value([])
    unfilled_state = reactive.Value([])
    status_state = reactive.Value('Ready. Enter passkey and click Load / Refresh data.')

    @reactive.Effect
    @reactive.event(input.load)
    def _load_data():
        passkey = (input.passkey() or '').strip()
        years = list(input.years() or [])

        if passkey == '':
            status_state.set('No passkey entered.')
            df_state.set(pd.DataFrame([]))
            master_state.set([])
            unfilled_state.set([])
            return

        if not years:
            status_state.set('No academic years selected.')
            df_state.set(pd.DataFrame([]))
            master_state.set([])
            unfilled_state.set([])
            return

        try:
            status_state.set('Loading data from Amion...')
            df = download_df_multi_year(years, passkey)

            if df.empty:
                status_state.set('Pulled 0 rows (or export empty).')
                df_state.set(pd.DataFrame([]))
                master_state.set([])
                unfilled_state.set([])
                return

            status_state.set('Building master rotation list...')
            master = build_master_rotations(df)

            df_state.set(df)
            master_state.set(master)
            unfilled_state.set([])

            status_state.set(
                'Loaded rows = {}, master rotations = {}.'.format(len(df), len(master))
            )

        except Exception as e:
            status_state.set('Load failed (did not crash UI): {}'.format(e))
            df_state.set(pd.DataFrame([]))
            master_state.set([])
            unfilled_state.set([])

    @reactive.Effect
    @reactive.event(input.check)
    def _check_month():
        month = (input.month() or '').strip()
        df = df_state.get()
        master = master_state.get()

        if df.empty or not master:
            status_state.set('No data/master list loaded. Click Load / Refresh data first.')
            unfilled_state.set([])
            return

        if not re.match(r'^\d{4}-\d{2}$', month):
            status_state.set('Invalid month format. Use YYYY-MM (example: 2026-02).')
            unfilled_state.set([])
            return

        try:
            unfilled = rotations_unfilled_in_month(df, master, month)
            unfilled_state.set(unfilled)
            status_state.set('Computed openings for {} (n = {}).'.format(month, len(unfilled)))
        except Exception as e:
            status_state.set('Check failed: {}'.format(e))
            unfilled_state.set([])

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
        return pd.DataFrame({'Rotation': unfilled_state.get()})

    @output
    @render.text
    def unfilled_list():
        unfilled = unfilled_state.get()
        if not unfilled:
            return ''
        return '\n'.join(['- {}'.format(r) for r in unfilled])

app = App(app_ui, server)