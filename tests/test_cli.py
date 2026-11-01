from conftest import EXAMPLES, PLAYBOOKS

from revloop.cli import main


def test_quickstart_runs_end_to_end(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    db = ["--db", str(tmp_path / "r.db")]
    pb = ["--playbooks", str(PLAYBOOKS), "--now", "2026-09-30"]
    main(db + ["init"])
    main(db + ["ingest", str(EXAMPLES / "artifacts")])
    main(db + ["run", "stalled-deal"] + pb)
    main(db + ["run", "competitor-mentioned"] + pb)
    main(db + ["approve", "1"])
    main(db + ["reject", "2"])
    main(db + ["approve", "3"])
    main(db + ["outcomes", "import", str(EXAMPLES / "outcomes.csv")])
    capsys.readouterr()

    main(db + ["report", "--now", "2026-11-01"])
    report = capsys.readouterr().out
    assert "stalled-deal\n  approved action : 1/1 advanced (100%)\n  holdout         : 0/1 advanced (0%)" in report
    assert "not enough data" in report

    main(db + ["context", "Northwind Robotics"])
    context = capsys.readouterr().out
    assert "book a call with your security team" in context and "superseded" not in context
