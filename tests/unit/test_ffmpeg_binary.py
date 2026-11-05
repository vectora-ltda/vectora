"""``backend/services/ffmpeg_binary.py`` — resolução do binário
ffmpeg/ffprobe. Mesmo padrão (e mesma ordem de prioridade) de
``backend/scheduling/nats_sidecar.py::_resolve_binary``: bundle congelado
→ PATH → árvore-fonte (``resources/``)."""

from __future__ import annotations

import backend.services.ffmpeg_binary as mod


class TestResolveFfmpeg:
    def test_prioriza_bundle_congelado_sobre_path_e_resources(
        self, monkeypatch, tmp_path
    ):
        """Ordem: bundle congelado → PATH → resources/. Um binário
        no bundle vence mesmo com PATH e resources também tendo um."""
        bundle_dir = tmp_path / "bundle"
        (bundle_dir / "ffmpeg").mkdir(parents=True)
        bundle_bin = bundle_dir / "ffmpeg" / mod._exe_name("ffmpeg")
        bundle_bin.write_text("bundle")

        resources_dir = tmp_path / "resources"
        resources_dir.mkdir()
        (resources_dir / mod._exe_name("ffmpeg")).write_text("vendored")

        monkeypatch.setattr(mod, "_frozen_bundle_bases", lambda: [bundle_dir])
        monkeypatch.setattr(mod, "_resources_dir", lambda: resources_dir)
        monkeypatch.setattr(mod.shutil, "which", lambda _name: "/usr/bin/ffmpeg")

        assert mod.resolve_ffmpeg() == str(bundle_bin)

    def test_sem_bundle_usa_path_do_sistema(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mod, "_frozen_bundle_bases", list)
        monkeypatch.setattr(mod, "_resources_dir", lambda: tmp_path / "resources")
        monkeypatch.setattr(mod.shutil, "which", lambda _name: "/usr/bin/ffmpeg")

        assert mod.resolve_ffmpeg() == "/usr/bin/ffmpeg"

    def test_sem_bundle_e_sem_path_cai_pro_vectora_resources(
        self, monkeypatch, tmp_path
    ):
        resources_dir = tmp_path / "resources"
        resources_dir.mkdir()
        ffprobe_bin = resources_dir / mod._exe_name("ffprobe")
        ffprobe_bin.write_text("vendored")

        monkeypatch.setattr(mod, "_frozen_bundle_bases", list)
        monkeypatch.setattr(mod, "_resources_dir", lambda: resources_dir)
        monkeypatch.setattr(mod.shutil, "which", lambda _name: None)

        assert mod.resolve_ffprobe() == str(ffprobe_bin)

    def test_nada_encontrado_em_lugar_nenhum_devolve_none_sem_lancar(
        self, monkeypatch, tmp_path
    ):
        """Erro/borda: sem bundle, sem PATH e sem `resources/` —
        devolve `None` sem lançar. O chamador (`media_native.py`) degrada
        a feature (erro tipado pro LLM), nunca derruba o backend
        (CLAUDE.md #11)."""
        monkeypatch.setattr(mod, "_frozen_bundle_bases", list)
        monkeypatch.setattr(mod, "_resources_dir", lambda: tmp_path / "vazio")
        monkeypatch.setattr(mod.shutil, "which", lambda _name: None)

        assert mod.resolve_ffmpeg() is None
        assert mod.resolve_ffprobe() is None
