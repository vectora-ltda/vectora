---
title: "Tutorial: Backend Python"
weight: 2
---

Crie um backend que responda a um método JSON-RPC do VEXT.

## Criar e implementar

```bash
vext init ./hello-extension
cd hello-extension
```

Edite `main.py`:

```python
def handle(method: str, params: dict[str, object]) -> dict[str, object]:
    if method != "greet":
        raise ValueError("método desconhecido")
    return {"message": f"Olá, {params.get('name', 'Vectora')}!"}
```

Declare no manifesto somente as capabilities necessárias.

## Validar e instalar

```bash
vext dev .
vext build . --output ../hello.vext
vext install ../hello.vext --root ~/.vectora/extensions --allow-unsigned
```

Use uma chave confiável antes de distribuir o artefato.
