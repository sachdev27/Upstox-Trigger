# write a flask simple app

import fileinput
from flask import Flask,request
app = Flask(__name__)

def update_env_file(file_path, key, new_value):
    with fileinput.FileInput(file_path, inplace=True, backup='.bak') as file:
        for line in file:
            if line.startswith(f'{key}='):
                print(f'{key}="{new_value}"')
            else:
                print(line, end='')

@app.route('/')
def hello_name():
   return 'Hello World'

@app.route('/callback')
def redirect_uri():
    code = request.args.get('code')
    update_env_file(".env","AUTH_CODE",code)
    return f'Hello World! Received code: {code}'

if __name__ == '__main__':
   app.run(debug=True,port=8210)
