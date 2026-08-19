# Interrupt OUT command channel (transport #3)

## Текущее состояние

Командный канал работает через два control-transfer транспорта:

- transport=feature: PC через hid_send_feature_report -> SET_REPORT(Feature).
- transport=output: PC через hid_write -> SET_REPORT(Output).

Оба проверены на железе. Ключевая находка: Windows шлёт SET_REPORT с wLength=16 (report id продублирован в data stage), handler в core HID.cpp это учитывает (скидывает первый байт).

Важно: настоящая мышь G305 (интерфейс MI_01) input-only. InputReportByteLength 9, OutputReportByteLength 0, FeatureReportByteLength 0. Ни OUT endpoint, ни Output/Feature reports у неё нет. Двунаправленный Input+Output (7/20/32 байта) сидит на отдельном интерфейсе MI_02 (vendor/HID++), это не мышь. Поэтому любой PC->Arduino канал на клоне мыши это добавка к fingerprint. Пользователь принял решение добавить interrupt OUT endpoint на интерфейс мыши (вариант A) как основной канал.

## Цель

Добавить третий транспорт transport=interrupt_out: interrupt OUT endpoint на существующем HID-интерфейсе. Существующие feature и output транспорты сохранить без изменений.

## Дизайн

PC-сторона: interrupt_out использует hid_write (то же, что output). Когда у устройства есть OUT endpoint и Output report, hidusb маршрутизирует hid_write в OUT endpoint, а не в SET_REPORT. Поэтому на PC используется тот же OutputReportTransport, только с новым кодом транспорта 3. Никаких новых PC-классов не нужно.

Устройство:

- HID_ модуль: с одного endpoint на два (IN + OUT).
- getInterface(): bNumEndpoints=2, добавить второй D_ENDPOINT(USB_ENDPOINT_OUT(...)).
- Дескриптор: vendor collection Output report (те же байты, что у output транспорта). Input report НЕ добавляем (обратный поток отложен).
- Чтение: опрос OUT endpoint в loop() через USB_Recv, результат в буфер CommandTransport.
- CommandTransport: новый класс InterruptOutTransport; выбор по hid_command_config.h.

Ключевое отличие от control-transfer: для interrupt OUT report id идёт ПЕРВЫМ байтом данных на проводе, то есть USB_Recv читает 16 байт [0x03][payload 15], первый байт = report id 3. В отличие от SET_REPORT, где report id в wValue.

## Шаги реализации

1) command_generator.py: константа TRANSPORT_INTERRUPT_OUT=3, TRANSPORT_CODES дополнить "interrupt_out"; validate_command_profile принимает его; generate_hid_command_config_h выдаёт его; generate_command_descriptor для interrupt_out делает Output report (0x91) как у output.

2) patcher.py patch_hid_command_core: в HID.h epType[1] -> epType[2] (массив), в HID.cpp конструктор PluggableUSBModule(1,1,...) -> (2,1,...), epType[1]=EP_TYPE_INTERRUPT_OUT, getInterface D_INTERFACE numEndpoints 1->2 и добавить D_ENDPOINT(USB_ENDPOINT_OUT(pluggedEndpoint+1), USB_ENDPOINT_TYPE_INTERRUPT, USB_EP_SIZE, 0x01). Marker-guarded, идемпотентно. ВАЖНО: это меняет endpoint allocation для ВСЕХ транспортов (OUT endpoint добавляется всегда, когда command.enabled), поэтому надо аккуратно: добавлять OUT endpoint только при transport=interrupt_out, иначе дескриптор endpoint для output/feature не должен включать OUT.

3) Core HID.cpp: API чтения OUT endpoint, например availableOutReport()/readOutReport(uint8_t* dst, uint8_t maxlen) через USB_Recv(pluggedOutEndpoint,...), опрос в loop. Нужен способ узнать номер OUT endpoint (pluggedEndpoint+1). Подумать: метод HID_::readCommandOut(buffer, len) который читает OUT FIFO.

4) HubCommand CommandTransport.h: класс InterruptOutTransport (available/read через USB_Recv OUT endpoint), getCommandTransport выбирает его по коду 3.

5) targets: задать transport interrupt_out (напр. generic_5btn) для проверки.

6) pc_client: в mouse_client.hpp маппинг HID_COMMAND_TRANSPORT 3 -> OutputReportTransport (hid_write). Больше ничего.

7) tests: обновить под три транспорта (дескриптор interrupt_out == output дескриптор, конфиг header код 3, патч core добавляет OUT endpoint только для interrupt_out).

8) Проверка: patch -> flash -> client_demo, убедиться что команды идут и физическая мышь работает.

## Готовсы / подводные камни

- 32U4 имеет 7 endpoint; IN(1)+OUT(2) влезает.
- OUT endpoint данные попадают в FIFO и читаются USB_Recv опросом (как CDC RX), НЕ через control ISR.
- Для interrupt OUT report на проводе [0x03][payload 15] = 16 байт, первый байт report id. Читающий код должен скидывать его (в отличие от SET_REPORT где id в wValue).
- Обратный поток (Arduino->PC данные через Input report) НЕ реализован, отложен. Capability (GET_REPORT Feature id 4) остаётся статичным мета.
- Runtime выбор транспорта в клиенте не нужен (решение пользователя): транспорт compile-time.
- Патч core добавляет OUT endpoint ТОЛЬКО при transport=interrupt_out. При output/feature OUT endpoint не добавлять, иначе дескриптор endpoint станет неверным и hid_write начнёт маршрутизироваться в несуществующий/лишний OUT.

## Файлы, которые трогает задача

src/arduino_hub/usbhid/command_generator.py
src/arduino_hub/core/patcher.py
libraries/HubCommand/src/CommandTransport.h
pc_client/include/hubclient/mouse_client.hpp
targets/*.json
tests/test_command_generator.py, tests/test_patcher.py
