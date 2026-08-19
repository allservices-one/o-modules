---
id: letter-001
date: 2026-08-19
status: draft — не надіслано
author: власник проєкту (надсилати від себе, не від Unitsoft)
---

# Лист до Odoo: доступ до метаданих Apps Store

## Куди надсилати

Окремої публічної адреси команди Apps Store **немає** — я перевірив
`apps.odoo.com/apps/vendor-guidelines` і `apps.odoo.com/contactus` (останній
віддає 403 автоматичним агентам, з браузера відкривається). У guidelines для
контактів указано лише загальну форму `odoo.com/contactus` і телефон
+32 2 290 34 90.

Тому: **форма на `odoo.com/contactus`**, тема — «Apps Store: request for
metadata access (research project)». Адреси не вигадувати: лист на
неперевірений `apps@` з великою ймовірністю просто зникне.

Другий канал, якщо через форму тиша два тижні: Odoo Community forum, розділ
про Apps Store, публічно. Публічне питання інколи отримує відповідь швидше за
приватне — і сам факт питання показує, що ми не крадемо дані.

Не надсилати з робочої скриньки Unitsoft: це приватний проєкт, і змішування
дасть Odoo привід читати лист як запит партнера з комерційним інтересом.

## Що в листі є і чого немає

Є: хто, що, конкретне прохання з трьох варіантів, і що ми даємо взамін.
Немає: скарг на `robots.txt`, натяків, що ми все одно це зробимо, і слова
«scraping». Прохання сформульоване так, щоб на нього можна було відповісти
одним рядком.

---

## Текст (англійською — надсилати цей)

> **Subject:** Apps Store: request for metadata access (independent research project)
>
> Hello,
>
> I run an independent, non-commercial index that tests which community Odoo
> modules actually install on which Odoo version. Every result comes from a real
> install run in a clean database — `odoo -i <module> --stop-after-init` — with
> the log and date published. It currently covers about 4,400 OCA modules across
> 18.0 and 19.0, and it is free and open: https://allservices.one
>
> One finding may interest you directly: eleven months after the 19.0 release,
> roughly 65% of the OCA modules present on 18.0 still have no 19.0 branch. I am
> happy to share the full dataset and methodology.
>
> I would like to extend the index beyond OCA, and I do not want to do that in a
> way you would object to. Your `robots.txt` disallows the paginated and search
> URLs on apps.odoo.com, so there is no route I consider legitimate for
> discovering the listing set, and I have not used one. Hence this message rather
> than a crawler.
>
> Would any of the following be possible?
>
> 1. A read-only export or API for listing metadata only — technical name,
>    display name, vendor, supported versions, licence, free/paid flag. No
>    descriptions, no screenshots, no pricing detail needed.
> 2. Or written permission to fetch the listing pages at a rate you specify
>    (I would propose one request per second, one pass per week, with an
>    identifying User-Agent and a contact address).
> 3. Or a simple no — in which case I will leave the Store out of the index
>    entirely, which is what I am doing today.
>
> What I can offer in return: full access to the dataset and to the per-vendor
> change feeds; results for the development line ahead of the 20.0 release, which
> may be useful to your own migration planning; and a standing commitment that
> nothing is published without the run log and date behind it. The index states
> plainly that it is independent and not affiliated with Odoo S.A. or the OCA,
> and it will continue to.
>
> Thank you for your time.
>
> Serhii Vinnikov
> https://allservices.one · <контактна адреса>

---

## Український переклад (для власного контролю, не надсилати)

Незалежний некомерційний індекс, який перевіряє, які модулі спільноти реально
встановлюються на яку версію Odoo. Кожен результат — справжній прогін у чистій
базі, з логом і датою. Зараз ~4 400 модулів OCA на 18.0 і 19.0.

Одна знахідка може бути цікава їм самим: через 11 місяців після релізу 19.0
близько 65% модулів OCA, які є на 18.0, досі не мають гілки 19.0.

Прохання — одне з трьох: (1) експорт або API лише з метаданими листингів;
(2) письмовий дозвіл забирати сторінки з указаною ними швидкістю; (3) або
пряме «ні», і тоді Store лишається поза індексом, як зараз.

Взамін: повний доступ до датасету і фідів по вендорах, результати по лінії
розробки до релізу 20.0, і зобов'язання не публікувати нічого без лога й дати.

## Що зробити після відправки

Записати дату відправки тут, у цьому файлі, і чекати. **До відповіді нічого не
змінюється:** Store лишається поза індексом (`inbox/0011`), розширення покриття
йде через GitHub (`inbox/0013 D`). Лист — не блокер, а відкриті двері.
