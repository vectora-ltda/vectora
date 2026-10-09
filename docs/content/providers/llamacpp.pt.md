---
title: llama.cpp e modelos locais
weight: 20
---

O Vectora pode usar um servidor llama.cpp externo ou um runtime gerenciado no dispositivo local.

## Endpoint externo

Em Configurações, informe a URL OpenAI-compatible, normalmente `http://127.0.0.1:8080/v1`. A chave é opcional para servidores locais. O botão de teste diferencia indisponibilidade, autenticação, resposta incompatível e catálogo vazio.

## Runtime gerenciado

O instalador consulta releases oficiais do projeto llama.cpp e grava o runtime no diretório de dados do usuário. Os arquivos são validados quando há checksum disponível; o Vectora não hospeda nem reempacota binários.

## Modelos Hugging Face

O catálogo apresenta metadados e uma classificação conservadora de compatibilidade. Pesos GGUF são baixados para o dispositivo e permanecem separados do runtime. O formato compatível não garante desempenho: memória depende do contexto, quantização, parâmetros e hardware.

## Privacidade e limites

Chaves não são exibidas em respostas. O sidecar gerenciado usa loopback e seu encerramento não interfere em servidores externos. JEV, Clef e Decision Models não fazem parte desta integração.
