PyInstaller --clean --onefile --hidden-import=PIL --hidden-import=PIL.Image --hidden-import=PIL.ImageTk --hidden-import=PIL._tkinter_finder --icon=images/p.ico --add-data "settings;settings" --add-data "images;images" --add-data "pdf;pdf" --name "Map Tile Generator v1.1" --distpath "Map Tile Generator v1.1" --upx-dir "C:\upx-5.2.1-win64" MapperTiles.pyw

pause