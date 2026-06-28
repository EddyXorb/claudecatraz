from catraz import doctor


def test_check_gitlab_url_set():
    f = doctor.Findings()
    doctor.check_gitlab({"GITLAB_URL": "https://gitlab.example.com"}, f)
    assert any(i[0] == doctor.OK and "gitlab.example.com" in i[2]
               for i in f.items)


def test_check_gitlab_url_unset():
    f = doctor.Findings()
    doctor.check_gitlab({}, f)
    assert any(i[0] == doctor.WARN and "GITLAB_URL" in i[2]
               for i in f.items)


def test_check_gitlab_url_empty():
    f = doctor.Findings()
    doctor.check_gitlab({"GITLAB_URL": ""}, f)
    assert any(i[0] == doctor.WARN for i in f.items)
