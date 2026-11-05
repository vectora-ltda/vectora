"""Vectora Connect — adapters de mensageria externa.

Cada adapter traduz o formato nativo de uma plataforma pro par
``IncomingMessage``/``OutgoingMessage`` de
``backend/services/gateway/messaging.py`` e devolve a resposta do agente —
nenhum deles reimplementa resolução de thread nem execução do agente.

As 4 plataformas conectam por conexão **outbound** (long polling do Telegram,
WebSocket Gateway do Discord, Socket Mode do Slack, polling IMAP do email):
nenhuma exige IP público, domínio ou túnel pelo Worker do gateway.

Credencial é **por instalação**: cada usuário cria o próprio bot/app e cola o
token nas Settings. Não existe bot central operado pelo Vectora.
"""

from __future__ import annotations
