"""`is_safe_shell_command` — regressão da blacklist original + fecho de
ofuscação trivial (achado da comparação de guardrails com o Hermes Agent:
`backend/services/security.py:108` comparava substring sem normalização,
então espaço duplo/`$IFS`/backslash-escape escapavam da blacklist)."""

from __future__ import annotations

import pytest

from backend.services.security import is_safe_shell_command


@pytest.mark.parametrize(
    "comando",
    [
        "rm -rf /",
        "rm -fr /tmp",
        "rm --no-preserve-root /",
        "rmdir /s /q C:\\",
        "rd /s /q C:\\",
        "mkfs.ext4 /dev/sda1",
        "format c:",
        "dd if=/dev/zero of=/dev/sda",
        "dd if=/dev/urandom of=/dev/sda",
        "shred /dev/sda",
        "wipe /dev/sda",
        "secure-delete /home",
        ":(){:|:&};:",
        "sudo rm -rf /",
        "sudo mkfs.ext4 /dev/sda1",
        "sudo dd if=/dev/zero of=/dev/sda",
        "sudo shred /dev/sda",
    ],
)
def test_blacklist_original_continua_bloqueando(comando: str) -> None:
    assert is_safe_shell_command(comando) is False


@pytest.mark.parametrize(
    "comando",
    [
        "git status",
        "git add .",
        "git commit -m 'fix'",
        "git push origin main",
        "npm install",
        "python script.py",
        "curl https://example.com",
        "rm arquivo.txt",
        "rm -r pasta_vazia",
        "ls -la",
    ],
)
def test_comandos_legitimos_continuam_permitidos(comando: str) -> None:
    """Erro/borda: a blacklist não pode virar falso positivo — comando comum
    do dia a dia do agente (inclusive `rm` sem `-rf`) precisa passar."""
    assert is_safe_shell_command(comando) is True


@pytest.mark.parametrize(
    "comando",
    [
        "rm  -rf /",  # espaço duplo
        "rm\t-rf\t/",  # tab no lugar de espaço
        "rm  -rf  /tmp",
        "sudo  rm -rf /",
    ],
)
def test_espacamento_ofuscado_e_bloqueado(comando: str) -> None:
    """Achado da comparação com Hermes: `_normalize_command_for_detection`
    colapsa espaços múltiplos antes de comparar — a blacklist original não."""
    assert is_safe_shell_command(comando) is False


@pytest.mark.parametrize(
    "comando",
    [
        "rm${IFS}-rf${IFS}/",
        "rm$IFS-rf$IFS/",
    ],
)
def test_ifs_como_separador_e_bloqueado(comando: str) -> None:
    """`$IFS`/`${IFS}` é um separador de campo shell válido, usado pra
    escapar filtros ingênuos de espaço — precisa expandir pra espaço antes
    de comparar."""
    assert is_safe_shell_command(comando) is False


def test_ifs_dentro_de_comando_legitimo_nao_quebra() -> None:
    """Erro/borda: `$IFS` poderia aparecer legitimamente numa string
    (ex. um script que imprime a variável) — a normalização não pode
    transformar todo uso de `IFS` em bloqueio indiscriminado do resto do
    comando."""
    assert is_safe_shell_command("echo $IFS") is True


@pytest.mark.parametrize(
    "comando",
    [
        "r\\m -rf /",
        "rm \\-rf /",
    ],
)
def test_backslash_escape_e_bloqueado(comando: str) -> None:
    """Backslash antes de uma letra comum (`\\m`, `\\-`) não tem efeito
    real no shell além de escapar o char seguinte — normalizar removendo
    o backslash fecha a ofuscação sem afetar comandos legítimos."""
    assert is_safe_shell_command(comando) is False


@pytest.mark.parametrize(
    "comando",
    [
        "rm -rfv /",
        "rm -vrf /",
        "rm -fvr /",
    ],
)
def test_ordem_de_flags_variada_e_bloqueada(comando: str) -> None:
    """`rm -rf`/`-fr` já eram cobertos; flags combinadas com outras letras
    (`-rfv`, `-vrf`) escapavam da comparação literal — cobertas agora por
    regex que reconhece `r` e `f` juntas em qualquer ordem/posição."""
    assert is_safe_shell_command(comando) is False


def test_vazio_e_permitido() -> None:
    assert is_safe_shell_command("") is True
