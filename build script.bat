PyInstaller --clean --onefile --hidden-import=PIL --hidden-import=PIL.Image --hidden-import=PIL.ImageTk --hidden-import=PIL._tkinter_finder --icon=images/1.ico --add-data "settings;settings" --add-data "images;images" --add-data "pdf;pdf" --name "Map Tile Generator v1.7" --distpath "Map Tile Generator v1.7" --upx-dir "C:\upx-5.2.1-win64" MapperTiles.pyw

pause