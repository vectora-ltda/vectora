---
title: llama.cpp y modelos locales
weight: 20
---

Vectora puede usar un servidor llama.cpp externo o un runtime administrado en el dispositivo local.

## Endpoint externo

Configure una URL compatible con OpenAI, normalmente `http://127.0.0.1:8080/v1`. La clave es opcional para servidores locales. La prueba distingue indisponibilidad, autenticación, respuesta incompatible y catálogo vacío.

## Runtime administrado

El instalador consulta las versiones oficiales de llama.cpp y guarda el runtime en los datos del usuario. Los artefactos se verifican cuando hay checksum; Vectora no aloja ni reempaqueta binarios.

## Modelos de Hugging Face

El catálogo muestra metadatos y una clasificación conservadora. Los pesos GGUF se descargan al dispositivo y permanecen separados del runtime. Un formato compatible no garantiza rendimiento.

## Privacidad y límites

Las claves nunca se devuelven en respuestas. El sidecar administrado usa loopback y detenerlo no afecta servidores externos. JEV, Clef y Decision Models quedan fuera de esta integración.
