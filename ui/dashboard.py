"""
dashboard.py — Code Guardian
Run with:  streamlit run ui/dashboard.py
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st
import pandas as pd

st.set_page_config(page_title="Code Guardian", page_icon=None, layout="wide", initial_sidebar_state="collapsed")

# Keepalive: prevents Streamlit Cloud from sleeping during judge evaluation
st.markdown("""
<script>
  setInterval(function() {
    fetch(window.location.href, {method: 'GET', cache: 'no-cache'});
  }, 30000);
</script>
""", unsafe_allow_html=True)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap');
html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
#MainMenu, footer, header { visibility: hidden; }
.block-container { padding: 2rem 2.5rem 4rem; max-width: 1280px; }
.stButton > button {
    background: #fff !important; color: #000 !important;
    border: 1px solid #e0e0e0 !important; border-radius: 6px !important;
    font-family: 'Inter', sans-serif !important; font-size: 13px !important;
    font-weight: 500 !important; padding: 6px 16px !important;
}
.stButton > button:hover { background: #f5f5f5 !important; border-color: #aaa !important; }
[data-testid="stTextInput"] input {
    font-family: 'JetBrains Mono', monospace !important; font-size: 13px !important;
    border: 1px solid #e0e0e0 !important; border-radius: 6px !important; background: #fafafa !important;
}
.pipeline-wrap { display:flex; align-items:center; gap:0; margin: 24px 0 20px; }
.pipe-step { display:flex; flex-direction:column; align-items:center; flex:1; }
.pipe-dot { width:10px; height:10px; border-radius:50%; background:#d4d4d4; margin-bottom:8px; }
.pipe-dot.active { background:#f59e0b; box-shadow:0 0 0 3px rgba(245,158,11,0.2); }
.pipe-dot.done   { background:#16a34a; }
.pipe-dot.error  { background:#dc2626; }
.pipe-connector  { height:1px; background:#e4e4e4; flex:1; margin-bottom:22px; }
.pipe-label { font-size:11px; font-weight:500; color:#6b7280; letter-spacing:0.05em; text-transform:uppercase; }
.pipe-label.active { color:#f59e0b; }
.pipe-label.done   { color:#16a34a; }
.log-terminal {
    background:#0f0f0f; border:1px solid #262626; border-radius:8px;
    padding:16px 20px; font-family:'JetBrains Mono',monospace; font-size:12px;
    line-height:1.8; color:#a3a3a3; min-height:80px; margin:12px 0 20px;
}
.log-ok  { color:#4ade80; } .log-warn { color:#fbbf24; }
.log-err { color:#f87171; } .log-dim  { color:#525252; }
.metric-row { display:flex; gap:12px; margin:24px 0 20px; }
.metric-card { flex:1; background:#fff; border:1px solid #e4e4e7; border-radius:8px; padding:16px 20px; }
.metric-num { font-size:28px; font-weight:600; color:#111; }
.metric-lbl { font-size:12px; color:#71717a; margin-top:2px; font-weight:500; letter-spacing:0.04em; text-transform:uppercase; }
.metric-card.pass .metric-num { color:#16a34a; }
.metric-card.fail .metric-num { color:#dc2626; }
.section-title { font-size:13px; font-weight:600; color:#111; letter-spacing:0.03em; text-transform:uppercase; margin:28px 0 12px; border-bottom:1px solid #f0f0f0; padding-bottom:8px; }
.status-bar { display:flex; align-items:center; gap:8px; padding:10px 16px; background:#fafafa; border:1px solid #e4e4e7; border-radius:6px; font-size:13px; color:#374151; margin-bottom:16px; }
.status-dot { width:8px; height:8px; border-radius:50%; flex-shrink:0; }
.status-dot.green { background:#16a34a; } .status-dot.red { background:#dc2626; }
.demo-hint { font-size:12px; color:#9ca3af; margin-top:8px; font-family:'JetBrains Mono',monospace; }
</style>
""", unsafe_allow_html=True)

# ── Header
st.markdown("""
<div style="display:flex;align-items:baseline;gap:12px;margin-bottom:4px;">
  <span style="font-size:22px;font-weight:600;color:#111;letter-spacing:-0.5px;">Code Guardian</span>
  <span style="font-size:12px;font-family:'JetBrains Mono',monospace;background:#f4f4f5;color:#71717a;padding:2px 8px;border-radius:4px;border:1px solid #e4e4e7;">v1.0.0</span>
</div>
<p style="font-size:13px;color:#6b7280;margin:0 0 28px;">Autonomous code review pipeline &mdash; AMD Instinct MI300X &middot; Llama 3.3 70B</p>
""", unsafe_allow_html=True)

# ── Input
c1, c2, c3 = st.columns([5, 1, 1])
with c1:
    repo_input = st.text_input("repo", placeholder="https://github.com/user/repo", label_visibility="collapsed")
with c2:
    if st.button("Load demo", use_container_width=True):
        st.session_state["use_demo"] = True
        st.rerun()
with c3:
    run = st.button("Run pipeline", type="primary", use_container_width=True)

if st.session_state.get("use_demo"):
    repo_input = "https://github.com/AbhikRao/code-guardian-demo"

st.markdown('<p class="demo-hint">⚠️ For best results use the demo repo or small repos (&lt;50 files). Large repos may hit the daily token limit.</p>', unsafe_allow_html=True)

# ── Pipeline track
STEPS = ["SCANNER", "FIXER", "TEST WRITER", "EXECUTOR", "REPORTER"]

MAX_BUGS_FIXED = 10  # must match agents/fixer.py

def pipeline_html(active, done_set):
    parts = []
    for i, name in enumerate(STEPS):
        if name in done_set:  dc, lc = "done",   "done"
        elif name == active:  dc, lc = "active", "active"
        else:                 dc, lc = "",        ""
        parts.append(
            '<div class="pipe-step">'
            '<div class="pipe-dot ' + dc + '"></div>'
            '<span class="pipe-label ' + lc + '">' + name + '</span>'
            '</div>'
        )
        if i < len(STEPS) - 1:
            parts.append('<div class="pipe-connector"></div>')
    return '<div class="pipeline-wrap">' + "".join(parts) + '</div>'

pipeline_ph = st.empty()
pipeline_ph.markdown(pipeline_html("", set()), unsafe_allow_html=True)

# ── Run
if run and repo_input:
    from core.orchestrator import PipelineState
    from agents.scanner     import ScannerAgent
    from agents.fixer       import FixerAgent
    from agents.test_writer import TestWriterAgent
    from agents.executor    import ExecutorAgent
    from agents.reporter    import ReporterAgent

    log_ph = st.empty()
    done_s = set()
    cur    = [""]
    logs   = []

    def upd(stage, msg, level="ok"):
        cur[0] = stage
        cls = {"ok": "log-ok", "warn": "log-warn", "err": "log-err"}.get(level, "")
        logs.append('<span class="log-dim">&rsaquo;</span> <span class="' + cls + '">[' + stage + ']</span> ' + msg)
        log_ph.markdown('<div class="log-terminal">' + "<br>".join(logs[-14:]) + "</div>", unsafe_allow_html=True)
        pipeline_ph.markdown(pipeline_html(cur[0], done_s), unsafe_allow_html=True)

    is_local = os.path.isdir(repo_input)
    state = PipelineState(
        repo_url   = "local" if is_local else repo_input,
        local_path = repo_input if is_local else ""
    )

    upd("SCANNER", "Cloning and scanning repository...")
    state = ScannerAgent().run(state)
    if state.error: st.error(state.error); st.stop()
    done_s.add("SCANNER")
    upd("SCANNER", "Found " + str(len(state.bug_report)) + " issue(s)")

    bugs_to_fix = min(len(state.bug_report), MAX_BUGS_FIXED)
    upd("FIXER", "Patching top " + str(bugs_to_fix) + " bug(s) by severity...")
    state = FixerAgent().run(state)
    if state.error: st.error(state.error); st.stop()
    done_s.add("FIXER")
    upd("FIXER", "Patched " + str(len(state.patched_files)) + " file(s)")

    upd("TEST WRITER", "Writing assertion-driven test suite...")
    state = TestWriterAgent().run(state)
    if state.error: st.error(state.error); st.stop()
    done_s.add("TEST WRITER")
    upd("TEST WRITER", "Generated " + str(len(state.test_files)) + " test file(s)")

    upd("EXECUTOR", "Running test suite in isolated sandbox...")
    state = ExecutorAgent().run(state)
    if state.error: st.error(state.error); st.stop()
    done_s.add("EXECUTOR")
    if state.tests_passed:
        upd("EXECUTOR", "All tests passed", "ok")
    else:
        upd("EXECUTOR", "Tests failed — " + state.failure_cause, "warn")

    upd("REPORTER", "Generating patch report...")
    state = ReporterAgent().run(state)
    done_s.add("REPORTER")
    upd("REPORTER", "Complete", "ok")
    pipeline_ph.markdown(pipeline_html("", done_s), unsafe_allow_html=True)

    dot = "green" if state.tests_passed else "red"
    msg = "Pipeline completed — all tests passed" if state.tests_passed else ("Pipeline completed with failures — " + state.failure_cause)
    st.markdown('<div class="status-bar"><div class="status-dot ' + dot + '"></div><span>' + msg + '</span></div>', unsafe_allow_html=True)

    test_count = sum(c.count("def test_") for c in state.test_files.values())
    pass_cls   = "pass" if state.tests_passed else "fail"
    pass_label = "Pass" if state.tests_passed else "Fail"
    st.markdown(
        '<div class="metric-row">'
        '<div class="metric-card"><div class="metric-num">' + str(len(state.bug_report)) + '</div><div class="metric-lbl">Bugs found</div></div>'
        '<div class="metric-card"><div class="metric-num">' + str(len(state.patched_files)) + '</div><div class="metric-lbl">Files patched</div></div>'
        '<div class="metric-card"><div class="metric-num">' + str(test_count) + '</div><div class="metric-lbl">Tests written</div></div>'
        '<div class="metric-card ' + pass_cls + '"><div class="metric-num">' + pass_label + '</div><div class="metric-lbl">Test result</div></div>'
        '</div>', unsafe_allow_html=True)

    if state.pr_url and state.pr_url.startswith("http"):
        st.markdown('<div style="padding:12px 16px;background:#f0fdf4;border:1px solid #bbf7d0;border-radius:6px;font-size:13px;color:#15803d;margin-bottom:20px;">Pull request opened &rarr; <a href="' + state.pr_url + '" style="color:#15803d;font-weight:600;">' + state.pr_url + '</a></div>', unsafe_allow_html=True)

    if state.bug_report:
        st.markdown('<div class="section-title">Bug Report</div>', unsafe_allow_html=True)
        rows = [{"Severity": b.get("severity","").upper(), "File": b.get("file",""), "Line": b.get("line",""), "Issue": b.get("issue","")} for b in state.bug_report]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True,
                     column_config={"Severity": st.column_config.TextColumn(width="small"),
                                    "Line": st.column_config.NumberColumn(width="small"),
                                    "File": st.column_config.TextColumn(width="medium")})

    if state.patched_files:
        st.markdown('<div class="section-title">Patched Files</div>', unsafe_allow_html=True)
        keys = list(state.patched_files.keys())
        for tab, key in zip(st.tabs(keys), keys):
            with tab: st.code(state.patched_files[key], language="python")

    if state.test_files:
        st.markdown('<div class="section-title">Generated Tests</div>', unsafe_allow_html=True)
        tkeys = list(state.test_files.keys())
        for tab, key in zip(st.tabs(tkeys), tkeys):
            with tab: st.code(state.test_files[key], language="python")

    if state.test_output:
        st.markdown('<div class="section-title">Test Output</div>', unsafe_allow_html=True)
        with st.expander("Show full pytest output", expanded=not state.tests_passed):
            st.code(state.test_output, language="text")

# ── Footer
st.markdown('<div style="margin-top:60px;padding-top:20px;border-top:1px solid #f0f0f0;display:flex;justify-content:space-between;"><span style="font-size:12px;color:#9ca3af;font-family:JetBrains Mono,monospace;">Code Guardian</span><span style="font-size:12px;color:#d1d5db;">AMD Developer Hackathon 2026</span></div>', unsafe_allow_html=True)
