from _check_provider_consistency import EXPECTED, check_consistency, provider_sets


def test_all_four_registration_sites_agree():
    assert not check_consistency(provider_sets())


def test_a_missing_picker_option_is_detected():
    sets = {"local setup": set(EXPECTED), "runtime status": set(EXPECTED), "runtime resolver": set(EXPECTED), "desktop picker": EXPECTED - {"doubao"}}
    assert "desktop picker" in check_consistency(sets)[0]
