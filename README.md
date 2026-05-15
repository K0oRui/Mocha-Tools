

```
 ███▄ ▄███▓ ▒█████   ▄████▄   ██░ ██  ▄▄▄         ▄▄▄█████▓ ▒█████   ▒█████   ██▓      ██████ 
▓██▒▀█▀ ██▒▒██▒  ██▒▒██▀ ▀█  ▓██░ ██▒▒████▄       ▓  ██▒ ▓▒▒██▒  ██▒▒██▒  ██▒▓██▒    ▒██    ▒ 
▓██    ▓██░▒██░  ██▒▒▓█    ▄ ▒██▀▀██░▒██  ▀█▄     ▒ ▓██░ ▒░▒██░  ██▒▒██░  ██▒▒██░    ░ ▓██▄   
▒██    ▒██ ▒██   ██░▒▓▓▄ ▄██▒░▓█ ░██ ░██▄▄▄▄██    ░ ▓██▓ ░ ▒██   ██░▒██   ██░▒██░      ▒   ██▒
▒██▒   ░██▒░ ████▓▒░▒ ▓███▀ ░░▓█▒░██▓ ▓█   ▓██▒     ▒██▒ ░ ░ ████▓▒░░ ████▓▒░░██████▒▒██████▒▒
░ ▒░   ░  ░░ ▒░▒░▒░ ░ ░▒ ▒  ░ ▒ ░░▒░▒ ▒▒   ▓▒█░     ▒ ░░   ░ ▒░▒░▒░ ░ ▒░▒░▒░ ░ ▒░▓  ░▒ ▒▓▒ ▒ ░
░  ░      ░  ░ ▒ ▒░   ░  ▒    ▒ ░▒░ ░  ▒   ▒▒ ░       ░      ░ ▒ ▒░   ░ ▒ ▒░ ░ ░ ▒  ░░ ░▒  ░ ░
░      ░   ░ ░ ░ ▒  ░         ░  ░░ ░  ░   ▒        ░      ░ ░ ░ ▒  ░ ░ ░ ▒    ░ ░   ░  ░  ░  
       ░       ░ ░  ░ ░       ░  ░  ░      ░  ░                ░ ░      ░ ░      ░  ░      ░  
                    ░                                                                         
                                                                                                
```
*Cross platform uploader for Mocha written in Python and designed to be compiled with pyinstaller*

# **WIP**                                             
|**CURRENT ISSUES**|
| :---- |
|<ul><li>Folder upload just dumps all files in root without creating new folder</li><li>Original file names not being listed</li><li>Unable to move files</li><li>Unable to create or view shares</li><li>Large file upload is not working correctly</li><li>Uploading to specific existing folders is not functioning</li></ul>|


## Requirements
- Python 3.10 or higher (can be downloader [here](https://www.python.org/downloads/)
- PyQt6 (can be installed with `pip install PyQt6`)
- requests (can be installed with `pip install requests`)
- pyinstaller (can be installed with `pip install pyinstaller`)
- A Mocha account and an API key

## Features
- Uploads files to Mocha with a simple drag and drop interface, or selection through file manager.
- Folder upload support (note issues listed above)
- Upload speed and progress indicators
- Create share links with all options from within the program
- Ability to view files and folders (note issues listed above)

## Potential? Ideas
| Idea | Complete? |
| :---- | :----: |
| Add download support for your own files | ❌ |
| Create android version | ❌ |
| Add to context menu (traditiional and Windows 11 (maybe idk how that works yet) for easy uploading | ❌ |
| Complete control over files, deletion, moving, sharing, etc. | ❌ |
| Debug and token management in its own tab | ✅ |

