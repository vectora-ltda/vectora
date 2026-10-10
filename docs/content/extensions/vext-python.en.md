---
title: "Tutorial: Python backend"
weight: 2
---

Create a backend that responds to a VEXT JSON-RPC method.

## Create and implement

```bash
vext init ./hello-extension
cd hello-extension
```

Edit `main.py`:

```python
def handle(method: str, params: dict[str, object]) -> dict[str, object]:
    if method != "greet":
        raise ValueError("unknown method")
    return {"message": f"Hello, {params.get('name', 'Vectora')}!"}
```

Declare only the capabilities the extension needs in `vectora-extension.json`.

## Validate and install

```bash
vext dev .
vext build . --output ../hello.vext
vext install ../hello.vext --root ~/.vectora/extensions --allow-unsigned
```

Use a trusted signing key before distributing the artifact.
