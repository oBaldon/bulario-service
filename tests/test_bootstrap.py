from bulario_service.__main__ import main


def test_application_bootstrap(capsys) -> None:
    main()

    captured = capsys.readouterr()

    assert captured.out.strip() == "InteliReg Bulário Service"