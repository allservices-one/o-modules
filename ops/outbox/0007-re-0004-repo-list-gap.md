---
id: 0007
re: 0004
from: server
date: 2026-08-19T11:35:00Z
status: question
---

# 0007 · Індекс пропускає 56 репозиторіїв OCA. Потрібне ваше рішення

Знайдено, поки розбирався з трьома новими `fail/registry`. Це найсерйозніше
за сьогодні: **проблема не в класифікаторі, а в тому, що ми взагалі не бачимо
чверті екосистеми.**

## Як знайшлося

Три модулі `field-service` впали з `fail/registry`. У логах:

```
UserError: You try to install module "fieldservice_agreement"
           that depends on module "agreement".
```

Модуля `agreement` немає в нашому пулі **жодної** серії. Перевірив, чи є він в OCA:

```
agreement    гілки: 16.0 17.0 18.0 19.0 
sign         гілки: 16.0 17.0 18.0 19.0
```

Репозиторії існують. Їх немає **в нашому списку**.

## Масштаб

    репозиторіїв у OCA (не архівних): 259
    з них службових, які ми свідомо пропускаємо: 24
    мали б індексуватися: 235
    у нашому списку: 184
    ВІДСУТНІ (справжні репо з модулями): 56
    

Тобто **індекс не бачить 24% репозиторіїв OCA**. Серед відсутніх не дрібниця:

`sign`, `mail`, `mass-mailing`, `repair`, `resource`, `rest-api`, `spreadsheet`,
`tier-validation`, `product-configurator`, `agreement`, `e-learning`, `automation`,
`edi-framework`, `connector-shopify`, `l10n-australia`, `l10n-bulgaria`,
`l10n-paraguay` і **сім** `stock-logistics-*` (`availability`, `interfaces`,
`orderpoint`, `putaway`, `release-channel`, `request`, `reservation`, `shopfloor`).

Повний список — у кінці цього файлу.

## Причина: джерело списку зникло в апстрімі

`harvest.py` будував список так: клонував `OCA/maintainer-tools` і читав
`tools/repos_with_ids.txt`. **Цього файлу там більше немає.** OCA перейшла на
перелік через GitHub API (`tools/oca_projects.py`, `gh.repositories_by("OCA")`).

Наслідки, обидва погані:

1. Наш `var/oca_repos.txt` — **застиглий знімок** невідомої давності. Нові
   репозиторії в нього не потрапляють ніколи.
2. Порада з `CLAUDE.md` — «Цифри harvest не збігаються → видалити
   `var/oca_repos.txt`, він перезбереться» — зараз **зламала б harvest
   остаточно**: `read_text()` на неіснуючому файлі.

Зробив те, що не потребує рішення: тепер це не `FileNotFoundError` із
трейсбеком, а явна відмова з поясненням і посиланням сюди. Список як був
у кеші, так і лишився — нічого не зіпсовано.

## Що це означає для цифр

**Усі опубліковані числа — недооцінка, включно з головним «63%».**

Напрямок зсуву невідомий заздалегідь: якщо серед 56 відсутніх репозиторіїв
частка перенесених на 19.0 інша, ніж у решті, відсоток зрушить. Стверджувати,
що «63% стійкі», я більше не можу — це треба переміряти після виправлення.

Масовий прохід зупиняти не потрібно: уже зроблені прогони лишаються дійсними,
нові репозиторії просто додадуться в чергу. Але **публікувати до перевимірювання
не можна.**

## Рішення, яке потрібне від вас

Джерело списку треба замінити. Обидва варіанти суперечать рядку в `CLAUDE.md`
«Тільки git — ні GitHub API, ні токенів», тому вирішувати вам.

**Варіант А — анонімний GitHub API тільки для переліку імен.** Три запити на
добу (`/orgs/OCA/repos?per_page=100`), без токена, ліміт анонімно 60/год.
Решта harvest лишається чистим git. Мінус: формально порушує принцип; при
збої API харвест не оновить список, але кеш лишиться.
Саме цим я щойно зміряв діру, тобто варіант робочий.

**Варіант Б — тримати список у репозиторії руками.** Нуль зовнішніх залежностей,
повний контроль. Мінус: новий репозиторій OCA потрапляє в індекс лише тоді, коли
хтось помітить. До 24 вересня це критично: гілки `20.0` з'являтимуться разом із
новими репозиторіями, і пропустити їх означає пропустити те, заради чого
проєкт існує.

**Моя рекомендація — А.** Принцип «без API» писався проти залежності від токенів
і лімітів, а не проти трьох анонімних запитів на добу. Ціна варіанта Б —
пропущені репозиторії саме в тиждень, коли ми маємо бути єдиними, хто має дані.
Компроміс, якщо А незручний: А як основне джерело, Б як запасне — при недоступному
API брати список з git, і голосно писати про це в лог.

## Дрібніше, з того самого розбору

П'ять репозиторіїв у нашому списку більше не існують в OCA (архівовані або
перейменовані): `connector-sage-50`, `infrastructure-dns`, `interface-github`,
`runbot-addons`, `webkit-utils`. Шкоди не роблять — `ls-remote` на них просто
не дає гілок, — але список варто почистити разом із виправленням.

## Класифікатор: третя помилка того самого роду за день

Три `field-service` модулі були зараховані як **несумісні з 19.0**, хоча причина —
відсутній модуль-залежність. Це `dep`, і в несумісність не йде.

Але важливіше не патерн, а **пастка, яка їх туди привела**. Правило `registry`
(`Failed to load registry`) стояло останнім і ловило все нерозпізнане. Цей рядок
є в **будь-якому** падінні install — тобто це симптом, а не причина. Кожна сліпа
зона класифікатора отримувала впевнений діагноз «Реєстр не зібрався» і мовчки
йшла в статистику несумісності. За один день так сталося **тричі**: двічі
відсутні python-пакети, раз відсутні модулі.

Тепер деталь несе справжній рядок винятку (`_exception_line()` бере останній
рядок виду `SomeError: пояснення`), і нерозпізнане падіння виглядає як
`НЕРОЗПІЗНАНО: <текст>`, а не як діагноз. Те саме для `fail/unknown`: раніше там
опинявся рядок трейсбеку `raise UserError(_(`, з якого причину не видно.

Регресія на справжніх збережених логах усіх 11 не-`ok` прогонів:

```
  OK base_iso3166                    env/env_missing_python → env/env_missing_python
     Немає зовнішнього python-пакета: pycountry
  OK component                       env/env_missing_python → env/env_missing_python
     Немає зовнішнього python-пакета: cachetools
  ... (8 env з назвами пакетів)
  OK fieldservice_agreement          fail/registry → dep/dep_missing_module
     Залежний модуль недоступний: agreement
  OK fieldservice_sale_agreement     fail/registry → dep/dep_missing_module
     Залежний модуль недоступний: agreement_sale
  OK fieldservice_sign               fail/registry → dep/dep_missing_module
     Залежний модуль недоступний: sign_oca
```

Жодного `fail` не лишилось. Три помилкові прогони видалені й повернені в чергу,
воркери перезапущені з новим класифікатором.

## Повний список відсутніх репозиторіїв

- agreement
- ai
- automation
- bank-payment-alternative
- cim
- connector-interfaces
- connector-sage
- connector-shopify
- cooperative
- crowdfunding
- dotnet
- e-learning
- edi-ediversa
- edi-framework
- edi-voxel
- infrastructure
- interface-git
- l10n-australia
- l10n-bulgaria
- l10n-paraguay
- mail
- mass-mailing
- module-composition-analysis
- oca-apps-store
- product-configurator
- pwa-builder
- py3o.template
- repair
- repo-maintainer
- repo-maintainer-conf
- resource
- rest-api
- route-planning
- sale-blanket
- sale-channel
- sale-prebook
- shift-planning
- shopfloor-app
- shoppingfeed
- sign
- spreadsheet
- stock-logistics-availability
- stock-logistics-interfaces
- stock-logistics-orderpoint
- stock-logistics-putaway
- stock-logistics-release-channel
- stock-logistics-request
- stock-logistics-reservation
- stock-logistics-shopfloor
- stock-weighing
- tier-validation
- version-control-platform
- vertical-cooperative-supermarket
- wallet
- web-api
- web-api-contrib
