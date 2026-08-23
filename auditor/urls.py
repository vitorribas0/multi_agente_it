from django.urls import path
from . import views
from . import gateway_views
from . import codex_views

app_name = "auditor"

urlpatterns = [
    # Chat
    path("api/chat/", views.chat_message, name="chat_message"),
    path("api/chat/stream/", views.chat_stream, name="chat_stream"),
    path("api/codex/status/", codex_views.codex_status, name="codex_status"),
    path("api/codex/skills/", codex_views.codex_skills, name="codex_skills"),
    path("api/codex/skills/<slug:slug>/", codex_views.codex_skill_delete, name="codex_skill_delete"),
    path("api/codex/chat/stream/", codex_views.codex_chat_stream, name="codex_chat_stream"),
    path(
        "api/codex/executions/<uuid:execution_id>/",
        codex_views.codex_execution_detail,
        name="codex_execution_detail",
    ),
    path(
        "api/codex/executions/<uuid:execution_id>/stop/",
        codex_views.codex_execution_stop,
        name="codex_execution_stop",
    ),
    path(
        "api/codex/interactions/<str:token>/respond/",
        codex_views.codex_interaction_respond,
        name="codex_interaction_respond",
    ),

    # Conversas
    path("api/conversations/", views.conversation_list, name="conversation_list"),
    path("api/conversations/<int:conv_id>/", views.conversation_detail, name="conversation_detail"),
    path("api/conversations/<int:conv_id>/stop/", views.chat_stop, name="chat_stop"),
    path("api/conversations/<int:conv_id>/rename/", views.conversation_rename, name="conversation_rename"),
    path("api/conversations/<int:conv_id>/delete/", views.conversation_delete, name="conversation_delete"),
    path("api/conversations/<int:conv_id>/dataset/", views.conversation_dataset, name="conversation_dataset"),

    # Knowledge Bases (RAG)
    path("api/kbs/", views.kbs_list, name="kbs_list"),
    path("api/conversations/<int:conv_id>/kbs/", views.conversation_kbs, name="conversation_kbs"),
    path("api/conversations/<int:conv_id>/kbs/save/", views.conversation_kbs_save, name="conversation_kbs_save"),

    # Conhecimentos (prompts de especialista cadastrados na tela)
    path("api/knowledge/", views.knowledge_list, name="knowledge_list"),
    path("api/knowledge/create/", views.knowledge_create, name="knowledge_create"),
    path("api/knowledge/<int:know_id>/update/", views.knowledge_update, name="knowledge_update"),
    path("api/knowledge/<int:know_id>/delete/", views.knowledge_delete, name="knowledge_delete"),
    path("api/conversations/<int:conv_id>/knowledge/", views.conversation_knowledge, name="conversation_knowledge"),
    path("api/conversations/<int:conv_id>/knowledge/save/", views.conversation_knowledge_save, name="conversation_knowledge_save"),

    # Agente da sessão (criado só para uma conversa, não democratizado)
    path("api/conversations/<int:conv_id>/session-agent/", views.session_agent_detail, name="session_agent_detail"),
    path("api/conversations/<int:conv_id>/session-agent/save/", views.session_agent_save, name="session_agent_save"),
    path("api/conversations/<int:conv_id>/session-agent/delete/", views.session_agent_delete, name="session_agent_delete"),
    path("api/session-agent/create-conversation/", views.session_agent_create_conversation, name="session_agent_create_conversation"),
    path("api/session-agent/extract-document/", views.session_agent_extract_document, name="session_agent_extract_document"),

    # Upload de tabelas
    path("api/upload/", views.upload_table, name="upload_table"),
    path("api/upload-batch/", views.upload_batch_docs, name="upload_batch_docs"),

    # Download de arquivos exportados pela tool exportar_dataset
    path("api/exports/<str:filename>", views.export_download, name="export_download"),
    path(
        "api/conversations/<int:conv_id>/artifacts/<str:filename>",
        views.conversation_artifact_download,
        name="conversation_artifact_download",
    ),

    # Playbooks (pipelines multi-agente autorados no canvas)
    path("api/playbooks/", views.playbook_list, name="playbook_list"),
    path("api/playbooks/create/", views.playbook_create, name="playbook_create"),
    path("api/playbooks/<int:pb_id>/", views.playbook_detail, name="playbook_detail"),
    path("api/playbooks/<int:pb_id>/update/", views.playbook_update, name="playbook_update"),
    path("api/playbooks/<int:pb_id>/delete/", views.playbook_delete, name="playbook_delete"),
    path("api/conversations/<int:conv_id>/playbook/save/", views.conversation_playbook_save, name="conversation_playbook_save"),

    # Configuração
    path("api/config/", views.config_overview, name="config_overview"),
    path("api/config/settings/", views.config_settings_save, name="config_settings_save"),
    path("api/config/agents/<slug:slug>/", views.config_agent_save, name="config_agent_save"),

    # ── Adapter do API Gateway ───────────────────────────────────────
    # Mesmas views das rotas /api/ acima, com a resposta traduzida para o
    # padrão exigido pelo style guide corporativo (camelCase + envelope
    # "data"). Ver auditor/gateway_views.py. As rotas /api/ continuam
    # servindo o frontend Angular sem alteração.
    path("gateway/chats", gateway_views.chat_message, name="gw_chat_message"),
    path("gateway/conversations", gateway_views.conversation_list, name="gw_conversation_list"),
    path("gateway/conversations/<int:conv_id>", gateway_views.conversation_detail, name="gw_conversation_detail"),
    path("gateway/uploads", gateway_views.upload_table, name="gw_upload_table"),
]
