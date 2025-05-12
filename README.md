elib_downloader предназначен для более удобного скачивания файлов из электронных библиотек.
- https://e.nlrs.ru/ (необходимо пройти регистрацию и указать в приложении свои логин и пароль; для ссылки вида https://e.nlrs.ru/open/1644 указать 1644)
- https://elib.rgo.ru/ (регистрация не нужна, указать полную ссылку вида https://elib.rgo.ru/safe-view/123456789/231378/1/MTM0OTVfVHVuZ3Vzc2tvLXJ1c3NraWkgc2xvdmFyJyBla3NwZWRpY2l5YSBwbyBpenVjaGVuaS5wZGY=)
- https://www.prlib.ru/ (регистрация не нужна, для ссылки вида https://www.prlib.ru/item/680723 указать 680723)
- https://pgpb.ru/ (регистрация не нужна, для ссылки вида https://pgpb.ru/digitization/document/4375 указать 4375)
- http://elib.shpl.ru/ru/nodes/9347-elektronnaya-biblioteka-gpib (регистрация не нужна, для ссылки вида http://elib.shpl.ru/pages/5006468/ указать 5006468)
- http://62.249.142.211:8083/read/183/pdf (регистрация не нужна, указать полную ссылку вида http://62.249.142.211:8083/read/183/pdf)
- https://catalog.libfl.ru/Record/BJVVV_604652 (регистрация нужна, указать ID книги вида bookID=BJVVV_604652 ЛИБО заказ как OrderId=920010)

При запуске приложения пользователь увидит окно консоли (закрывать его не нужно), а затем графический интерфейс программы.  
Приложение эмулирует ручные действия пользователя по копированию страниц.  
Если у вас по каким-либо причинам у пользователя не открывается сайт библиотеки, то приложение также не сможет выполнить скачивание.  
Чтобы избежать излишней нагрузки на серверы библиотек, приложение выполняет скачивание с искусственными паузами, поэтому может работать продолжительное время.

## Windows (если у вас *не* установлен Python)

Используйте готовый exe-файл из раздела [Releases](https://github.com/gisly/elib_downloader/releases)

## Linux / macOS
В этой инструкции подразумевается, что у вас:
- скачан репозиторий 
```sh
git clone https://github.com/gisly/elib_downloader.git;
cd elib_downloader;
```
- установлен Python версии == 3.11
- есть возможность написать команду для создания виртуального окружения:
```sh
pip install virtualenv
```
```sh
python -m virtualenv venv
```


### Создание виртуального окружения + зависимости
```sh
python -m virtualenv venv;
source venv/bin/activate;
pip install -r requirements.txt;
```
### Запуск
```
source venv/bin/activate;
python main.py;
```


## Windows (если у вас установлен Python)
В этой инструкции подразумевается, что у вас:
- скачан репозиторий 
```sh
git clone https://github.com/gisly/elib_downloader.git
cd elib_downloader
```
- установлен Python версии == 3.11
- Возможность написать команду для создания виртуального окружения:
```sh
pip install virtualenv
```
```sh
python -m virtualenv venv
```

### Создание виртуального окружения + зависимости
```sh
python -m virtualenv venv
cd venv
cd Scripts
activate
cd ..
cd ..
pip install -r requirements.txt
```

### Запуск
```sh
cd venv
cd Scripts
activate
cd ..
cd ..
python main.py
```

##  Сборка исполняемого файла для Windows
```sh
cd venv
cd Scripts
activate
cd ..
cd ..
nicegui-pack --onefile --name "elib_downloader" main.py
```