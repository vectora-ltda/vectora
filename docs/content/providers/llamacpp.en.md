---
title: llama.cpp and local models
weight: 20
---

Vectora can use an external llama.cpp server or a managed runtime on the local device.

## External endpoint

Configure an OpenAI-compatible URL, usually `http://127.0.0.1:18080/v1`. A key is optional for local servers. The connection test distinguishes unavailable, authentication, incompatible response, and empty catalog states.

## Docker Model Runner

When Docker Model Runner is enabled, Vectora uses the local endpoint `http://127.0.0.1:12434` and its OpenAI contract at `/engines/v1`. Provider Routing checks the Docker CLI, tests the connection, and prepares the selected model; a model can be entered as `hf.co/Qwen/Qwen3-0.6B`. These operations run on the local device with structured arguments and time limits, without exposing a Docker socket to a hosted service. The endpoint can also be used by OpenAI-compatible clients such as Open WebUI.

## Managed runtime

The installer reads official llama.cpp releases and stores the runtime in the user data directory. Artifacts are checked when a checksum is available; Vectora does not host or repackage binaries.

## Hugging Face models

The catalog shows metadata and conservative compatibility classification. GGUF weights are downloaded to the device and remain separate from the runtime. Compatible format does not guarantee performance: memory depends on context, quantization, parameters, and hardware.

## Privacy and limits

Keys are never returned in responses. The managed sidecar binds to loopback and stopping it does not affect external servers. JEV, Clef, and Decision Models are outside this integration. Docker Desktop with Model Runner enabled is required; without it, the UI reports the local runtime as unavailable.
