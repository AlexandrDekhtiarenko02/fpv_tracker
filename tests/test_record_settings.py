"""record.sh не должен стирать настройки борта перед ручным заходом."""
import io
import os
import shutil
import stat
import subprocess
import tempfile


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
tmp = tempfile.mkdtemp(prefix="record_settings_")


def _write_executable(path, text):
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)


try:
    script = os.path.join(tmp, "record.sh")
    shutil.copy2(os.path.join(ROOT, "record.sh"), script)
    os.chmod(script, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

    cfg = os.path.join(tmp, "local_settings.py")
    with io.open(cfg, "w", encoding="utf-8") as f:
        f.write(
            "# настройки испытательного борта\n"
            "OBSERVE_ONLY = False\n"
            "TARGET_LAT = 50.4501\n"
            "TARGET_LON = 30.5234\n"
            "GPS_EXPECTED = True\n"
        )

    fake_bin = os.path.join(tmp, "bin")
    os.mkdir(fake_bin)
    _write_executable(os.path.join(fake_bin, "sudo"), "#!/bin/sh\nexit 0\n")
    _write_executable(os.path.join(fake_bin, "sleep"), "#!/bin/sh\nexit 0\n")
    _write_executable(os.path.join(tmp, "status.sh"),
                      "#!/bin/sh\necho status-ok\n")

    env = dict(os.environ)
    env["PATH"] = fake_bin + os.pathsep + env.get("PATH", "")

    print("=== 1. Включение записи сохраняет настройки и включает наблюдение ===")
    subprocess.check_call(["bash", script, "on"], cwd=tmp, env=env,
                          stdout=subprocess.DEVNULL)
    text = io.open(cfg, encoding="utf-8").read()
    for setting in ("TARGET_LAT = 50.4501", "TARGET_LON = 30.5234",
                    "GPS_EXPECTED = True"):
        assert setting in text, "record.sh удалил настройку: %s" % setting
    assert text.count("OBSERVE_ONLY = True") == 1, (
        "запись образцов должна однозначно включать OBSERVE_ONLY")
    assert "RECORD_FRAMES = True" in text
    assert "RECORD_HIRES = False" in text

    print("=== 2. Выключение записи не включает управление самовольно ===")
    subprocess.check_call(["bash", script, "off"], cwd=tmp, env=env,
                          stdout=subprocess.DEVNULL)
    text = io.open(cfg, encoding="utf-8").read()
    assert "OBSERVE_ONLY = True" in text
    assert "RECORD_FRAMES = False" in text
    assert "RECORD_HIRES = False" in text
    assert "TARGET_LAT = 50.4501" in text
    assert "TARGET_LON = 30.5234" in text
    print("OK: настройки борта сохранены, ручное управление защищено")
finally:
    shutil.rmtree(tmp)
