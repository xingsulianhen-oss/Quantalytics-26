打包软件的命令
pyinstaller --noconfirm --onedir --console --clean --name "Quantalytics_Gold" --hidden-import="talib.stream" --hidden-import="scipy.special._cdflib" --hidden-import="pyqtgraph.opengl" --collect-all="tbb" main_ui.py
放入config.json,gold_price_cache.xlsx,tbb12.dll,_internal文件夹中放入akshare文件夹，backtesting文件夹