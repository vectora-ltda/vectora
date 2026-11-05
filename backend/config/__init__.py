"""Schema declarativo de configuração — fonte única de verdade para CLI e
REST (ver ``registry.py`` para o racional completo). Importar este pacote
popula os registries globais a partir de ``fields.py`` (escalar) e
``collection_fields.py`` (coleção).
"""

from __future__ import annotations

from backend.config import (
    collection_fields as _collection_fields,
)
from backend.config import (
    fields as _fields,
)
from backend.config.collections import (
    CollectionSettingField,
    DuplicateCollectionFieldError,
    UserScopedCollectionField,
    all_collections,
    all_user_scoped_fields,
    collection_field,
    collections_for_category,
    get_collection_field,
    get_user_scoped_field,
    user_scoped_field,
    user_scoped_fields_for_category,
)
from backend.config.registry import (
    DuplicateSettingFieldError,
    SettingField,
    all_categories,
    all_fields,
    fields_for_category,
    get_field,
    setting_field,
)

__all__ = [
    "CollectionSettingField",
    "DuplicateCollectionFieldError",
    "DuplicateSettingFieldError",
    "SettingField",
    "UserScopedCollectionField",
    "all_categories",
    "all_collections",
    "all_fields",
    "all_user_scoped_fields",
    "collection_field",
    "collections_for_category",
    "fields_for_category",
    "get_collection_field",
    "get_field",
    "get_user_scoped_field",
    "setting_field",
    "user_scoped_field",
    "user_scoped_fields_for_category",
]
