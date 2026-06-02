"""
Version alterna de chat_rag.py con interfaz tipo NeuralLegacy.

Reutiliza el backend de chat_rag.py:
  - get_graph_context
  - ask_openai
  - chat_fn
  - grafo visual
  - radar de ejes

Uso:
  python D:\Jenson\HostIA\chat_rag_neural.py
"""

from __future__ import annotations

import gradio as gr

from chat_rag import (
    NEO4J_URI,
    OPENAI_CHAT_MODEL,
    OPENAI_EMBED_MODEL,
    TOP_K,
    build_graph_html,
    build_radar_html,
    chat_fn,
    initialize_axes,
    parse_answer_to_graph,
)


NEURAL_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

html, body, .gradio-container {
  margin: 0 !important;
  padding: 0 !important;
  min-height: 100vh !important;
  background: #f4f7fb !important;
  font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif !important;
}

.gradio-container {
  max-width: none !important;
}

footer, .built-with, .api-docs {
  display: none !important;
}

#neural-shell {
  height: 100vh;
  display: grid;
  grid-template-columns: 305px 600px minmax(480px, 1fr);
  overflow: hidden;
  background: #f4f7fb;
}

#neural-sidebar {
  background: linear-gradient(180deg, #043263 0%, #072146 48%, #061832 100%);
  color: #fff;
  display: flex;
  flex-direction: column;
  min-height: 100vh;
  box-shadow: 8px 0 32px rgba(7, 27, 58, 0.18);
}

#neural-sidebar .logo-wrap {
  padding: 28px 24px;
  border-bottom: 1px solid rgba(255,255,255,0.10);
  display: flex;
  align-items: center;
  gap: 16px;
}

.logo-brain {
  width: 58px;
  height: 58px;
  border-radius: 20px;
  background: rgba(255,255,255,0.08);
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: inset 0 0 0 1px rgba(255,255,255,0.08);
}

.side-menu {
  padding: 20px;
  flex: 1;
  overflow-y: auto;
}

.menu-btn {
  width: 100%;
  border: 0;
  border-radius: 18px;
  padding: 15px 18px;
  margin-bottom: 12px;
  color: rgba(255,255,255,0.78);
  background: transparent;
  display: flex;
  align-items: center;
  gap: 14px;
  font-size: 15px;
  font-weight: 700;
  text-align: left;
}

.menu-btn.active {
  background: rgba(73,165,255,0.18);
  color: #fff;
  box-shadow: inset 0 0 0 1px rgba(73,165,255,0.25);
}

.glass-card {
  margin: 0 20px 20px;
  padding: 18px;
  border-radius: 20px;
  background: rgba(255,255,255,0.08);
  border: 1px solid rgba(255,255,255,0.12);
  backdrop-filter: blur(8px);
}

#neural-chat {
  height: 100vh;
  min-height: 0;
  background: #fff;
  border-right: 1px solid #dde6f2;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.chat-header {
  height: 120px;
  padding: 0 32px;
  border-bottom: 1px solid #dde6f2;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.status-pill {
  background: #ecfdf5;
  color: #059669;
  border: 1px solid #a7f3d0;
  padding: 9px 18px;
  border-radius: 999px;
  font-size: 13px;
  font-weight: 700;
}

#neural-chatbot {
  flex: 1;
  border: 0 !important;
  background: #fff !important;
}

#neural-chatbot .wrap,
#neural-chatbot .bubble-wrap {
  background: #fff !important;
}

#neural-chatbot .message {
  border-radius: 18px !important;
  box-shadow: 0 4px 20px rgba(7, 27, 58, 0.08) !important;
}

#neural-input-zone {
  padding: 24px 32px 22px;
  border-top: 1px solid #dde6f2;
  background: #fff;
  flex: 0 0 auto;
  display: block !important;
  visibility: visible !important;
}

#center-empty {
  flex: 1 1 auto;
  min-height: 0;
  display: flex !important;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}

#neural-input-row {
  align-items: center !important;
  gap: 12px !important;
  padding: 8px 10px 8px 18px;
  border: 1px solid #dde6f2;
  border-radius: 20px;
  background: #fff;
  box-shadow: 0 8px 24px rgba(7, 27, 58, 0.08);
}

#neural-input textarea,
#neural-input input {
  border: 0 !important;
  box-shadow: none !important;
  color: #071b3a !important;
  font-size: 16px !important;
}

#neural-input label,
#neural-input .container {
  border: 0 !important;
  box-shadow: none !important;
}

#neural-send {
  min-width: 48px !important;
  width: 48px !important;
  height: 48px !important;
  border-radius: 999px !important;
  padding: 0 !important;
  border: 0 !important;
  background: #0065ff !important;
  color: #fff !important;
  font-size: 18px !important;
  box-shadow: 0 8px 18px rgba(0,101,255,0.25) !important;
}

#neural-clear {
  border-radius: 14px !important;
  border: 1px solid #dde6f2 !important;
  color: #60708a !important;
  background: #fff !important;
}

#neural-panel {
  min-height: 100vh;
  padding: 28px;
  background: #f4f7fb;
  overflow: hidden;
}

.panel-card {
  height: calc(100vh - 56px);
  background: #fff;
  border: 1px solid #dde6f2;
  border-radius: 28px;
  box-shadow: 0 12px 36px rgba(7, 27, 58, 0.08);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.panel-title {
  padding: 24px 26px;
  border-bottom: 1px solid #dde6f2;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

#right-chat-area {
  flex: 1;
  min-height: 0;
  padding: 16px 18px 0;
  overflow: hidden;
}

#neural-chatbot > label,
#neural-chatbot .label-wrap {
  display: none !important;
}

#right-tools {
  border-top: 1px solid #dde6f2;
  margin-top: 12px;
  max-height: 44vh;
  overflow: auto;
}

#neural-tabs .tab-nav {
  padding-left: 18px;
  border-bottom: 1px solid #dde6f2 !important;
}

#neural-tabs .tabitem {
  max-height: 38vh;
  overflow: auto;
  padding: 16px !important;
  background: #fff !important;
}

#debug-out textarea {
  font-family: "Cascadia Code", Consolas, monospace !important;
  font-size: 12px !important;
}

@media (max-width: 1180px) {
  #neural-shell {
    grid-template-columns: 260px minmax(420px, 1fr);
  }
  #neural-panel {
    display: none;
  }
}
"""


def _brain_logo() -> str:
    return """
    <svg viewBox="0 0 120 120" xmlns="http://www.w3.org/2000/svg" style="width:46px;height:46px">
      <path d="M48 16C30 17 18 31 18 48c0 9 4 17 10 22-2 15 9 29 25 30 8 12 28 9 32-6 14-2 25-14 25-29 0-11-6-21-15-26 0-17-14-31-31-31-7 0-13 2-18 6Z"
        stroke="#49A5FF" stroke-width="4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
      <circle cx="38" cy="40" r="4" fill="#2DCCCD"/>
      <circle cx="62" cy="28" r="4" fill="#49A5FF"/>
      <circle cx="78" cy="48" r="4" fill="#2DCCCD"/>
      <circle cx="48" cy="68" r="4" fill="#49A5FF"/>
      <circle cx="76" cy="78" r="4" fill="#2DCCCD"/>
      <path d="M38 40L62 28L78 48L48 68L76 78M62 28L48 68M38 40L48 68"
        stroke="#49A5FF" stroke-width="2" fill="none" stroke-linecap="round"/>
    </svg>
    """


def _empty_graph_html() -> str:
    return (
        "<div style='height:100%;min-height:520px;display:flex;align-items:center;justify-content:center;"
        "background:#fff;color:#60708a;font-family:Inter,sans-serif'>"
        "<div style='text-align:center'>"
        "<div style='font-size:54px;color:#b8c7da;margin-bottom:20px'>▦</div>"
        "<div style='font-size:22px;font-weight:800;color:#071b3a;margin-bottom:8px'>Chat Principal</div>"
        "<div style='font-size:16px;line-height:1.6'>Las respuestas del asistente se muestran en este panel.</div>"
        "</div></div>"
    )


def _empty_radar_html() -> str:
    return build_radar_html({})


def _clear_state():
    return [], [], _empty_graph_html(), "", _empty_radar_html()


def build_neural_ui():
    with gr.Blocks(
        title="NeuralLegacy GraphRAG",
        css=NEURAL_CSS,
        theme=gr.themes.Base(),
    ) as demo:
        state = gr.State([])

        with gr.Row(elem_id="neural-shell"):
            with gr.Column(elem_id="neural-sidebar", scale=0, min_width=305):
                gr.HTML(
                    f"""
                    <div class="logo-wrap">
                      <div class="logo-brain">{_brain_logo()}</div>
                      <div>
                        <div style="font-size:24px;font-weight:800;line-height:1;color:#fff">
                          Neural<span style="color:#49A5FF">Legacy</span>
                        </div>
                        <div style="font-size:13px;color:rgba(255,255,255,.82);font-weight:700;margin-top:6px">by BBVA</div>
                      </div>
                    </div>
                    <div class="side-menu">
                      <div style="font-size:12px;color:rgba(219,234,254,.72);font-weight:800;text-transform:uppercase;letter-spacing:.08em;margin:0 0 18px 4px">
                        Herramientas IA
                      </div>
                      <button class="menu-btn active" onclick="document.querySelectorAll('.menu-btn').forEach(b=>b.classList.remove('active')); this.classList.add('active'); document.getElementById('panel-title-text').innerText='Chat Principal'; document.getElementById('panel-subtitle-text').innerText='Respuestas del asistente, relaciones y ejes de análisis'; return false;"><span>💬</span><span>Chat Principal</span></button>
                      <button class="menu-btn" onclick="document.querySelectorAll('.menu-btn').forEach(b=>b.classList.remove('active')); this.classList.add('active'); document.getElementById('panel-title-text').innerText='Business Graph'; document.getElementById('panel-subtitle-text').innerText='Explora relaciones entre componentes'; return false;"><span>🔗</span><span>Business Graph</span></button>
                      <button class="menu-btn" onclick="document.querySelectorAll('.menu-btn').forEach(b=>b.classList.remove('active')); this.classList.add('active'); document.getElementById('panel-title-text').innerText='Functional Discovery'; document.getElementById('panel-subtitle-text').innerText='Descubrimiento funcional desde COBOL, JCL, mallas y rutinas'; return false;"><span>▣</span><span>Functional Discovery</span></button>
                      <button class="menu-btn" onclick="document.querySelectorAll('.menu-btn').forEach(b=>b.classList.remove('active')); this.classList.add('active'); document.getElementById('panel-title-text').innerText='What-If Simulator'; document.getElementById('panel-subtitle-text').innerText='Análisis de impacto y escenarios de cambio'; return false;"><span>⚡</span><span>What-If Simulator</span></button>
                      <button class="menu-btn" onclick="document.querySelectorAll('.menu-btn').forEach(b=>b.classList.remove('active')); this.classList.add('active'); document.getElementById('panel-title-text').innerText='Gap Analysis'; document.getElementById('panel-subtitle-text').innerText='Brechas, riesgos y elementos pendientes de análisis'; return false;"><span>⚠</span><span>Gap Analysis</span></button>
                    </div>
                    <div class="glass-card">
                      <div style="color:rgba(255,255,255,.78);font-size:13px;margin-bottom:8px">Sesión</div>
                      <div style="display:flex;align-items:center;gap:8px;font-weight:800;color:#fff">
                        <span style="width:10px;height:10px;border-radius:50%;background:#34d399;display:inline-block"></span>
                        Activa
                      </div>
                      <div style="color:rgba(255,255,255,.78);font-size:13px;margin-top:20px;margin-bottom:8px">Modelo</div>
                      <div style="font-weight:800;color:#fff">Asistente Mainframe</div>
                    </div>
                    <div style="margin:0 20px 24px;padding:16px 0;border-top:1px solid rgba(255,255,255,.1);border-bottom:1px solid rgba(255,255,255,.1);display:flex;gap:12px;align-items:center">
                      <div style="width:46px;height:46px;border-radius:50%;background:#0065FF;display:flex;align-items:center;justify-content:center;font-weight:800">GR</div>
                      <div style="min-width:0">
                        <div style="font-weight:800;color:#fff">GraphRAG</div>
                        <div style="font-size:12px;color:rgba(219,234,254,.78);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">COBOL · JCL · Mallas · Rutinas</div>
                      </div>
                    </div>
                    <div style="padding:0 20px 26px;font-size:30px;font-weight:800;letter-spacing:.04em;color:#fff">BBVA</div>
                    """
                )

            with gr.Column(elem_id="neural-chat", scale=0, min_width=600):
                gr.HTML(
                    """
                    <div class="chat-header">
                      <div>
                        <div style="font-size:24px;font-weight:800;color:#071B3A">Asistente Mainframe</div>
                        <div style="display:flex;align-items:center;gap:8px;margin-top:10px;color:#60708A;font-weight:600">
                          <span style="width:10px;height:10px;border-radius:50%;background:#10b981"></span>
                          Conectado
                        </div>
                      </div>
                      <div class="status-pill">Conectado</div>
                    </div>
                    """
                )
                gr.HTML(
                    """
                    <div style="width:100%;display:flex;align-items:center;justify-content:center;padding:32px">
                      <div style="text-align:center;max-width:380px">
                        <div style="width:96px;height:96px;border-radius:50%;background:#EEF4FF;display:flex;align-items:center;justify-content:center;margin:0 auto 24px;color:#0065FF;font-size:42px">💬</div>
                        <div style="font-size:20px;font-weight:800;color:#071B3A;margin-bottom:12px">¡Hola! Soy tu asistente Mainframe</div>
                        <div style="color:#3E5270;line-height:1.7;font-size:15px">
                          Escribe tu consulta abajo.<br>
                          La respuesta aparecerá en el panel derecho.
                        </div>
                      </div>
                    </div>
                    """,
                    elem_id="center-empty",
                )
                with gr.Column(elem_id="neural-input-zone"):
                    with gr.Row(elem_id="neural-input-row"):
                        txt_input = gr.Textbox(
                            placeholder="Pregunta sobre mallas, jobs o DB2...",
                            show_label=False,
                            scale=10,
                            max_lines=4,
                            elem_id="neural-input",
                        )
                        btn_send = gr.Button("➤", variant="primary", scale=0, elem_id="neural-send")
                    with gr.Row():
                        gr.HTML(
                            "<div style='flex:1;text-align:center;color:#60708A;font-size:13px;padding-top:10px'>"
                            "Presiona Enter para enviar · Shift + Enter para nueva línea</div>"
                        )
                        btn_clear = gr.Button("Limpiar chat", variant="secondary", scale=0, elem_id="neural-clear")

            with gr.Column(elem_id="neural-panel", scale=1):
                with gr.Column(elem_classes=["panel-card"]):
                    gr.HTML(
                        """
                        <div class="panel-title">
                          <div>
                            <div id="panel-title-text" style="font-size:20px;font-weight:800;color:#071B3A">Chat Principal</div>
                            <div id="panel-subtitle-text" style="font-size:14px;color:#60708A;margin-top:4px">Respuestas del asistente, relaciones y ejes de análisis</div>
                          </div>
                          <div style="display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end">
                            <span style="font-size:12px;color:#0065FF;background:#EEF4FF;border:1px solid #DDE6F2;border-radius:999px;padding:7px 12px;font-weight:800">Neo4j</span>
                            <span style="font-size:12px;color:#059669;background:#ECFDF5;border:1px solid #A7F3D0;border-radius:999px;padding:7px 12px;font-weight:800">GraphRAG</span>
                          </div>
                        </div>
                        """
                    )
                    with gr.Column(elem_id="right-chat-area"):
                        chatbot = gr.Chatbot(
                            label="",
                            height=560,
                            elem_id="neural-chatbot",
                            show_copy_button=True,
                            type="messages",
                        )
                    with gr.Accordion("Business Graph, análisis de ejes y debug", open=False, elem_id="right-tools"):
                        with gr.Tabs(elem_id="neural-tabs"):
                            with gr.TabItem("Business Graph"):
                                graph_out = gr.HTML(value=_empty_graph_html())
                            with gr.TabItem("Análisis de Ejes"):
                                radar_out = gr.HTML(value=_empty_radar_html())
                            with gr.TabItem("Debug"):
                                gr.Markdown(
                                    f"""
| Parámetro | Valor |
|---|---|
| Neo4j URI | `{NEO4J_URI}` |
| Modelo chat | `{OPENAI_CHAT_MODEL}` |
| Modelo embedding | `{OPENAI_EMBED_MODEL}` |
| Documentos RAG por consulta | `{TOP_K}` |
"""
                                )
                                debug_out = gr.Textbox(
                                    label="Contexto RAG + keywords extraídas",
                                    lines=18,
                                    max_lines=30,
                                    interactive=False,
                                    placeholder="Aparecerá tras cada consulta...",
                                    elem_id="debug-out",
                                )

        btn_send.click(
            fn=chat_fn,
            inputs=[txt_input, state],
            outputs=[txt_input, chatbot, graph_out, debug_out, radar_out],
        )
        txt_input.submit(
            fn=chat_fn,
            inputs=[txt_input, state],
            outputs=[txt_input, chatbot, graph_out, debug_out, radar_out],
        )
        btn_clear.click(
            fn=_clear_state,
            outputs=[chatbot, state, graph_out, debug_out, radar_out],
        )

    return demo


if __name__ == "__main__":
    try:
        initialize_axes()
    except Exception as exc:
        print(f"[neural-ui] Aviso: no se pudieron inicializar ejes automaticamente: {exc}")

    app = build_neural_ui()
    app.launch(server_name="127.0.0.1", server_port=7862, inbrowser=True, share=True)
