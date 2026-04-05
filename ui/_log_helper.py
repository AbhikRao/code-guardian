    war_room   = st.empty()
    log_box    = st.empty()
    state_ref  = {"active": "SCANNER"}   # mutable dict avoids nonlocal
    done_set   = set()
    log_lines  = []

    def log(stage, msg):
        emoji_map = {"SCANNER":"🔍","FIXER":"🔧","TEST WRITER":"✍️",
                     "EXECUTOR":"⚡","REPORTER":"📬","RETRY":"🔄","ERROR":"❌"}
        em = emoji_map.get(stage, "•")
        log_lines.append(f"`{em} {stage}` — {msg}")
        log_box.markdown("\n\n".join(log_lines[-12:]))
        state_ref["active"] = stage
        with war_room.container():
            render_war_room(state_ref["active"], done_set)
