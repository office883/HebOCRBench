from hebocrbench.form_metrics import evaluate_form


def test_missing_and_hallucinated_fields_are_separate():
    gold = [
        {"field_id": "name", "value_text": "רות"},
        {"field_id": "id", "value_text": "123456789"},
    ]
    pred = [
        {"field_id": "name", "value_text": "רות"},
        {"field_id": "extra", "value_text": "מומצא"},
    ]
    result = evaluate_form(gold, pred)
    assert result["missing_fields"] == 1
    assert result["hallucinated_fields"] == 1
    assert result["field_presence_f1"] == 0.5
    assert result["value_exact_rate"] == 0.5


def test_field_value_gcer_catches_wrong_digits():
    gold = [{"field_id": "amount", "value_text": "₪1,234.50"}]
    pred = [{"field_id": "amount", "value_text": "₪1,243.50"}]
    result = evaluate_form(gold, pred)
    assert result["value_exact_rate"] == 0.0
    assert result["value_gcer"] > 0
