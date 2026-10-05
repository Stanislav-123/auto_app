import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.parser import merge_missing_reply, parse_app_message
from src.formatter import is_parse_complete

IOS_MESSAGE = """\
Новое IOS приложение 🍏

Searock
https://apps.apple.com/ru/app/searock/id6786185431

Bundle: cos.seasetify.offrock

App Bundle ID IOS: 6786185431

Backend Domain: yondra36.sbs

Appsflyer - есть

Чья прила: ZMApps

Пуши - APNs

Сайт разработчика: Имеется

Backend: Новый 

Комментарий: Приложение по новому формату, здесь нету фаербейза и линки в нем. Домен уже внутри приложения. Домен покупать не надо. Также на прилке APNs пуши нужен тест по токену

После базового теста выдаем в паблик
"""

ANDROID_MESSAGE = """\
Новое Android приложение 🤖

Gаtes оf Оlympus 
https://play.google.com/store/apps/details?id=com.aurumatlas.eonatelier

Bundle: com.aurumatlas.eonatelier

Appsflyer: есть
Название в Firebase: build-propelytix

Чья прила: Сторонний разработчик

Комментарий: Это прилка AppSolution, но мы ее выкупаем по формату AppMarket как у сторонних девов
Такие данные Андрей прислал я думаю вы знаете как прилки Android заводить его в систему
Если каких то данных не будет то скажите запрошу у него
Главное отмечать в системе как сторонний разработчик а не AppSolution !

После QA теста выдаем в паблик
"""


class TestParser(unittest.TestCase):
    def test_ios_message(self):
        app = parse_app_message(IOS_MESSAGE)

        self.assertEqual(app.platform.value, "ios")
        self.assertEqual(app.name, "Searock")
        self.assertEqual(
            app.store_url,
            "https://apps.apple.com/ru/app/searock/id6786185431",
        )
        self.assertEqual(app.bundle, "cos.seasetify.offrock")
        self.assertEqual(app.store_id, "id6786185431")
        self.assertEqual(app.backend_domain, "yondra36.sbs")
        self.assertTrue(app.appsflyer)
        self.assertEqual(app.owner, "ZMApps")
        self.assertEqual(app.pushes, "APNs")
        self.assertEqual(app.developer_site, "Имеется")
        self.assertEqual(app.backend, "Новый")
        self.assertEqual(app.backend_url, "https://neonflightgold777.cfd/apps")
        self.assertEqual(app.missing_fields, [])

    def test_android_message(self):
        app = parse_app_message(ANDROID_MESSAGE)

        self.assertEqual(app.platform.value, "android")
        self.assertEqual(app.name, "Gаtes оf Оlympus")
        self.assertEqual(
            app.store_url,
            "https://play.google.com/store/apps/details?id=com.aurumatlas.eonatelier",
        )
        self.assertEqual(app.bundle, "com.aurumatlas.eonatelier")
        self.assertEqual(app.store_id, "com.aurumatlas.eonatelier")
        self.assertTrue(app.appsflyer)
        self.assertEqual(app.firebase_name, "build-propelytix")
        self.assertEqual(app.owner, "Сторонний разработчик")
        self.assertEqual(app.backend_url, "https://myappadmin.top/qize4nmp/sites")
        self.assertEqual(app.missing_fields, [])

    def test_platform_from_ios_url_without_header(self):
        msg = """\
TEST_lalala
https://apps.apple.com/us/app/twr-repairly/id6804963670

Bundle: cot.cross.repairly

Appsflyer: есть
Чья прила: ZMApps
Пуши: APNs
Сайт разработчика: Имеется
Backend: Новый
"""
        app = parse_app_message(msg)
        self.assertEqual(app.platform.value, "ios")
        self.assertEqual(app.store_id, "id6804963670")
        self.assertEqual(app.bundle, "cot.cross.repairly")
        self.assertTrue(is_parse_complete(app))

    def test_merge_platform_reply(self):
        incomplete = parse_app_message("TEST\nBundle: com.example.app")
        self.assertEqual(incomplete.platform.value, "unknown")
        completed = merge_missing_reply(incomplete, "Android")
        self.assertEqual(completed.platform.value, "android")
        self.assertEqual(completed.store_id, "com.example.app")


if __name__ == "__main__":
    # Quick demo output when run directly
    for label, msg in [("iOS", IOS_MESSAGE), ("Android", ANDROID_MESSAGE)]:
        result = parse_app_message(msg)
        print(f"\n=== {label} ===")
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))

    unittest.main()
