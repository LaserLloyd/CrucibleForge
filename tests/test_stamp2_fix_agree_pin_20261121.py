"""Stamp-2 fix pass, finding 5: `agree --parts chat/packs45` refuses when --reader is not the pinned reader."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_release45_packs import mkfolder, pack_row  # noqa: E402

from crucibleforge import agree as AG  # noqa: E402
from crucibleforge import rplong_mean as RM  # noqa: E402


def _ns(folder, reader, plan=False):
    return argparse.Namespace(folders=[str(folder)], reader=[reader], parts="chat/packs45", adult=False,
                              cap_usd=None, plan=plan, out=None, judge_devices="2,3", judge_parallel=None,
                              judge_allow_devices="3", judge_instance_name=None, reader_thinking="on")


def test_a_reader_that_is_not_the_pin_is_refused_with_no_call(tmp_path, monkeypatch, capsys):
    from crucibleforge import cli, judge
    d = mkfolder(tmp_path, "m1", "deepseek-m1", [pack_row("LRV1-pilots-mess")])
    gf = AG.graded_family("deepseek-m1", None)
    pin = RM.pinned_reader(gf, RM.load_readers(), True)
    other = "studioforge:Qwen/Qwen3.8-27B"
    if RM.canonical_id(other.split(":", 1)[1]) == pin.id:
        other = "studioforge:Qwen/Qwen3.5-122B"
    cfg = {"providers": {"studioforge": {"type": "studioforge", "base_url": "http://x"}}, "models": []}
    boom = []

    def no_call(*a, **k):
        boom.append(1)
        raise AssertionError("a model or lease call was made")
    monkeypatch.setattr(judge, "_load_judge", no_call)
    monkeypatch.setattr(judge, "JudgeClient", no_call)
    monkeypatch.setattr(judge, "acquire_judge_lease", no_call)
    monkeypatch.setattr(cli, "maintenance_marker_present", lambda: False)
    assert cli.cmd_agree(_ns(d, other), cfg) == 2
    assert not boom
    assert "pinned reader" in capsys.readouterr().err
