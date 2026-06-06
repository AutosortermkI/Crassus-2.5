import importlib


def test_paper_ledger_records_events_append_only_and_materializes_preflight_only(tmp_path, monkeypatch):
    module = importlib.import_module("paper_ledger")
    ledger = importlib.reload(module)
    monkeypatch.setattr(ledger, "LOCAL_STORE", tmp_path / "paper_ledger.json")
    monkeypatch.setenv("AzureWebJobsStorage", "UseDevelopmentStorage=true")
    monkeypatch.setenv("PAPER_STARTING_CASH", "25000")

    signal_event = ledger.record_ledger_event(
        "signal_received",
        correlation_id="corr-1",
        payload={"ticker": "AAPL"},
        parsed={"ticker": "AAPL", "side": "buy", "mode": "stock", "price": 189.5},
    )
    preflight_event = ledger.record_ledger_event(
        "broker_preflight",
        correlation_id="corr-1",
        broker="tastytrade",
        execution={"ok": True, "status_code": 200, "body": {"dry_run": True, "order_id": "dry-run-1"}},
    )

    events = ledger.get_ledger_events(limit=10)
    account = ledger.get_paper_account()

    assert [event["event_type"] for event in events] == ["broker_preflight", "signal_received"]
    assert signal_event["event_id"] != preflight_event["event_id"]
    assert account["paper_fill_policy"] == "preflight_only"
    assert account["starting_cash"] == 25000.0
    assert account["cash"] == 25000.0
    assert account["total_equity"] == 25000.0
    assert account["open_positions"] == []
    assert account["realized_pl"] == 0.0
    assert account["unrealized_pl"] == 0.0
    assert account["event_count"] == 2


def test_trade_lifecycle_records_signal_and_dry_run_preflight(tmp_path, monkeypatch):
    module = importlib.import_module("paper_ledger")
    ledger = importlib.reload(module)
    monkeypatch.setattr(ledger, "LOCAL_STORE", tmp_path / "paper_ledger.json")
    monkeypatch.setenv("AzureWebJobsStorage", "UseDevelopmentStorage=true")

    events = ledger.record_trade_lifecycle(
        payload={"ticker": "AAPL"},
        parsed={"ticker": "AAPL", "side": "buy", "mode": "stock", "price": 189.5},
        execution={"ok": True, "status_code": 200, "body": {"broker": "tastytrade", "dry_run": True}},
        correlation_id="corr-2",
    )

    assert [event["event_type"] for event in events] == ["signal_received", "broker_preflight"]
    assert events[1]["broker"] == "tastytrade"
    assert events[1]["execution"]["body"]["dry_run"] is True


def test_paper_account_marks_positions_from_quote_cache(tmp_path, monkeypatch):
    ledger_module = importlib.import_module("paper_ledger")
    ledger = importlib.reload(ledger_module)
    market_module = importlib.import_module("market_data")
    market_data = importlib.reload(market_module)
    monkeypatch.setattr(ledger, "LOCAL_STORE", tmp_path / "paper_ledger.json")
    monkeypatch.setattr(market_data, "LOCAL_STORE", tmp_path / "market_data.json")
    monkeypatch.setenv("AzureWebJobsStorage", "UseDevelopmentStorage=true")
    monkeypatch.setenv("PAPER_STARTING_CASH", "10000")

    ledger.record_ledger_event(
        "paper_fill",
        correlation_id="corr-fill",
        fill={"symbol": "AAPL", "side": "buy", "qty": 2, "price": 100.0},
    )
    market_data.record_quote({
        "source": "tastytrade_dxlink",
        "event_type": "Trade",
        "symbol": "AAPL",
        "last": 110.0,
        "timestamp": "2026-06-06T16:01:00+00:00",
    })

    account = ledger.get_paper_account()

    assert account["cash"] == 9800.0
    assert account["total_equity"] == 10020.0
    assert account["unrealized_pl"] == 20.0
    assert account["open_positions"][0]["current_mark"] == 110.0
