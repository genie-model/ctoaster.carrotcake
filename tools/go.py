import os, os.path, sys, shutil, argparse, glob, signal
import subprocess as sp
import platform as plat
import datetime as dt
try:
    import queue as qu
    import threading as thr
    import tkinter as tk
    from tkinter import font as tkFont
    from tkinter import messagebox as tkMessageBox
    from tkinter import ttk
    gui_available = True
except ImportError:
    gui_available = False

import utils as U


if gui_available:
    # GUI CODE

    def iter_except(function, exception):
        """Works like builtin 2-argument `iter()`, but stops on `exception`."""
        try:
            while True:
                yield function()
        except exception:
            return

    def kill_after(r, p, countdown):
        if p.poll() is None:
            countdown -= 1
            if countdown < 0:
                p.kill()
            else:
                r.after(1000, kill_after, r, p, countdown)
                return
        p.wait()

    class Application(tk.Frame):
        def clear(self):
            self.stop()
            self.text.delete('1.0', tk.END)

        def clean(self):
            self.clear()
            clean(False)

        def cleaner(self):
            self.clear()
            clean(True)

        def build(self):
            self.clear()
            self.buttons_active(False)
            build(lambda: self.buttons_active(True))

        def run(self):
            self.clear()
            self.buttons_active(False)
            def c():
                run(lambda: self.buttons_active(True))
            build(c)

        def stop(self):
            if self.proc:
                p = self.proc
                self.proc = None
                self.cont = None
                self.contrest = None
                p.terminate()
                kill_after(self.root, p, countdown=5)
                message('CANCELLED')
                self.buttons_active(True)

        def done(self):
            self.stop()
            self.quit()

        def message(self, s):
            self.stop()
            self.text.insert(tk.END, 79 * '*' + '\n')
            self.text.insert(tk.END, '\n')
            self.text.insert(tk.END, '    ' + s + '\n')
            self.text.insert(tk.END, '\n')
            self.text.insert(tk.END, 79 * '*' + '\n')
            self.text.insert(tk.END, '\n')
            self.text.see(tk.END)

        def line(self, s):
            self.text.insert(tk.END, s + '\n')
            self.text.see(tk.END)

        def createWidgets(self):
            self.job_label_font = tkFont.Font(weight='bold')
            job = os.path.relpath(os.curdir, U.ctoaster_jobs)
            self.job_label = ttk.Label(self, text='Job: ' + job,
                                       font=self.job_label_font)
            self.button_frame = ttk.Frame(self)
            self.content_frame = ttk.Frame(self)
            self.action_buttons = [ ]
            action_button_defs = [['Run',     self.run],
                                  ['Build',   self.build],
                                  ['Clean',   self.clean],
                                  ['Cleaner', self.cleaner]]
            for r in range(len(action_button_defs)):
                lab, act = action_button_defs[r]
                self.action_buttons.append(ttk.Button(self.button_frame,
                                                      text=lab, command=act))
            self.quit_button = ttk.Button(self.button_frame,
                                          text='Quit', command=self.done)
            self.cancel_button = ttk.Button(self.button_frame, text='Cancel',
                                            command=self.stop)
            self.text = tk.Text(self.content_frame)
            self.text_scroll = ttk.Scrollbar(self.content_frame,
                                             command=self.text.yview)
            self.text['yscrollcommand'] = self.text_scroll.set

            self.winfo_toplevel().rowconfigure(0, weight=1)
            self.winfo_toplevel().columnconfigure(0, weight=1)
            self.grid(sticky=tk.N+tk.E+tk.S+tk.W)
            self.columnconfigure(0, weight=0)
            self.columnconfigure(1, weight=1)
            self.rowconfigure(0, weight=0)
            self.rowconfigure(1, weight=1)
            self.job_label.grid(column=0, row=0, columnspan=2, sticky=tk.W,
                                padx=10, pady=5)
            self.button_frame.grid(column=0, row=1, sticky=tk.N+tk.W, pady=5)
            self.content_frame.rowconfigure(0, weight=1)
            self.content_frame.columnconfigure(0, weight=1)
            self.content_frame.grid(column=1, row=1,
                                    sticky=tk.N+tk.E+tk.S+tk.W, padx=5)
            self.text.grid(column=0, row=0, sticky=tk.N+tk.E+tk.S+tk.W, pady=5)
            self.text.rowconfigure(0, weight=1)
            self.text.columnconfigure(0, weight=1)
            self.text_scroll.grid(column=1, row=0, sticky=tk.N+tk.S, pady=5)
            for r in range(len(action_button_defs)):
                self.action_buttons[r].grid(column=0, row=r,
                                            sticky=tk.E+tk.W, padx=5)
            self.quit_button.grid(column=0, row=len(action_button_defs),
                                  sticky=tk.E+tk.W, padx=5)
            self.cancel_button.grid(column=0, row=len(action_button_defs)+1,
                                    sticky=tk.E+tk.W+tk.S, padx=5, pady=10)

        def buttons_active(self, state):
            s = ['disabled'] if not state else ['!disabled']
            for b in self.action_buttons: b.state(s)

        def __init__(self, root=None):
            self.root = root
            self.proc = None
            tk.Frame.__init__(self, root)
            self.createWidgets()

        # Manage subsidiary process.
        def manage(self, cmd, logfp, cont, *rest):
            self.cont = cont
            self.contrest = rest
            self.proc = sp.Popen(cmd, stdout=sp.PIPE, stderr=sp.STDOUT, encoding='utf-8')  # Added encoding
            q = qu.Queue()
            t = thr.Thread(target=self.reader_thread, args=[q, logfp]).start()
            self.update(q)

        # Background thread: reads from subsidiary process.
        def reader_thread(self, q, logfp):
            for line in iter(self.proc.stdout.readline, ''):
                q.put(line)
                logfp.write(line)
            q.put(None)

        # Runs in GUI thread...
        def update(self, q):
            for line in iter_except(q.get_nowait, qu.Empty):
                if line is None:
                    status = self.proc.wait() if self.proc else -1
                    self.proc = None
                    if self.cont:
                        cont = self.cont
                        self.cont = None
                        contrest = self.contrest if self.contrest else None
                        self.contrest = None
                        if contrest:
                            cont(status, *contrest)
                        else:
                            cont(status)
                    return
                else:
                    self.text.insert(tk.END, line)
                    self.text.see(tk.END)
            self.root.after(40, self.update, q)


# This script can run as a GUI or command line tool.

gui = True if len(sys.argv) == 1 else False
if gui and not gui_available:
    sys.exit('GUI operation is not available!')


# cTOASTER configuration.

if not U.read_ctoaster_config():
    if gui:
        tk.Tk().withdraw()
        tkMessageBox.showerror('cTOASTER not set up',
                               'cTOASTER not set up: run the setup-ctoaster script!')
        sys.exit()
    else:
        sys.exit('cTOASTER not set up: run the setup-ctoaster script!')
###scons = os.path.join(U.ctoaster_root, 'tools', 'scons', 'scons.py')
scons = 'scons'	# [replaced the included scons v.2 distribution with what is hopefully installed scons v.3 ...]

def console_message(s):
    print('')
    print(79 * '*')
    print('')
    print('    ' + s)
    print('')
    print(79 * '*')
    print('')

def console_line(s):
    print(s)

runner = None

def cleanup(signum, frame):
    global runner
    if runner: runner.terminate()
    runner = None
    raise IOError('Terminated')

def console_manage(cmd, logfp, cont, *rest):
    global runner
    runner = None
    signal.signal(signal.SIGTERM, cleanup)
    runner = sp.Popen(cmd, stdout=sp.PIPE, stderr=sp.STDOUT, encoding='utf-8')  # Added encoding
    while True:
        line = runner.stdout.readline()  # Removed .decode('utf8')
        if not line: break
        logfp.write(line)
        logfp.flush()
        print(line, end='')
    res = runner.wait()
    runner = None
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    cont(res, *rest)


#Defining the default values for build_type and progress

build_type = 'ship'
progress = True

# Command line arguments.

parser = argparse.ArgumentParser(description='Model build and run commands')
subparsers = parser.add_subparsers(dest='command', help='Commands')

# Subparser for clean commands
clean_parser = subparsers.add_parser('clean', help='Clean results')
cleaner_parser = subparsers.add_parser('cleaner', help='Clean results and model build')
clean_build_parser = subparsers.add_parser('clean-build', help='Just clean model build')

# Subparser for build and run commands
for cmd in ['build', 'run']:
    cmd_parser = subparsers.add_parser(cmd, help=f'{cmd.capitalize()} model')
    cmd_parser.add_argument('build_type', nargs='?', default='ship', choices=U.build_types, help='Build type')
    cmd_parser.add_argument('--no-progress', action='store_false', dest='progress', help='Disable progress output')

# Subparser for platform commands
set_platform_parser = subparsers.add_parser('set-platform', help='Set explicit build platform')
set_platform_parser.add_argument('platform', help='Platform name')
clear_platform_parser = subparsers.add_parser('clear-platform', help='Clear explicit build platform')

args = parser.parse_args()

# Handling commands based on parsed arguments
if args.command in ['clean', 'cleaner', 'clear-platform', 'clean-build']:
    # Handle cleaning commands
    pass  # Implement the command-specific logic here
elif args.command == 'set-platform':
    platform = args.platform
    # Implement platform setting logic here
elif args.command in ['build', 'run']:
    build_type = args.build_type
    progress = args.progress
    # Implement build or run logic here
else:
    parser.print_help()
    sys.exit(1)


# Model configuration for job.

model_config = U.ModelConfig(build_type)
model_dir = model_config.directory()
exe_name = 'carrotcake-' + build_type + '.exe' if build_type else 'carrotcake.exe'


# Clean up output directories for this job and (optionally) build
# directories for model setup for this job.

def clean(clean_model):
    clean_msg = "CLEANING MODEL RESULTS AND BUILD" if clean_model else "CLEANING MODEL RESULTS"
    message(f'{clean_msg}...')
    if clean_model:
        model_config.clean()  # calls method 'clean' for class ModelConfig [utils.py]
        for exe in glob.iglob('carrotcake-*.exe'):
            os.remove(exe)  # finds and removes 'carrotcake-*.exe' files
        if os.path.exists('build.log'):
            os.remove('build.log')  # removes 'build.log' if it exists
    if os.path.exists('run.log'):
        os.remove('run.log')  # removes 'run.log' if it exists
    for d, _, fs in os.walk('output'):
        for f in fs:
            os.remove(os.path.join(d, f))  # removes files in 'output' directory


def clean_build():
    message('REMOVING BUILD' + '...')
    if os.path.exists(model_dir):
        model_config.clean()


# Build model.

def build(cont):
    model_config.setup()
    model_dir = model_config.directory()
    if os.path.exists(os.path.join('config', 'platform-name')):
        print('Manual platform file has been set by set-platform')
        if not os.path.exists(os.path.join(model_dir, 'config')):
            os.makedirs(os.path.join(model_dir, 'config'))
        if not os.path.exists(os.path.join(model_dir, 'config', 'platform-name')):
            print('Manual platform does not have platform-name file, copying')
            shutil.copy(os.path.join('config', 'platform-name'), os.path.join(model_dir, 'config'))
    cmd = [scons, '-q', '-C', model_dir]
    cmd = [sys.executable] + cmd
    try:
        result = sp.run(cmd, stdout=sp.DEVNULL, stderr=sp.DEVNULL, check=True)
        need_build = False
    except sp.CalledProcessError:
        need_build = True

    if not need_build:
        message('Build is up to date')
        shutil.copy(os.path.join(model_dir, 'carrotcake.exe'), os.path.join(os.curdir, exe_name))
        if cont: cont()
        return

    message(f'BUILDING: {model_config.display_model_version}')
    with open(os.path.join(model_dir, 'build.log'), 'w') as logfp:
        rev = f'rev={model_config.display_model_version}'
        cmd = [scons, '-C', model_dir, rev, f'progress={"1" if progress else "0"}']
        manage(cmd, logfp, build2, cont)

def build2(result, cont):
    shutil.copy(os.path.join(model_dir, 'build.log'), os.curdir)
    if result == 0:
        line('')
        message('Build OK')
        shutil.copy(os.path.join(model_dir, 'carrotcake.exe'),
                    os.path.join(os.curdir, exe_name))
        if cont: cont()
    else:
        message('BUILD FAILED: see build.log for details')


# Run model.

tstart = None
tend = None

def run(cont=None):
    global tstart
    message(f'RUNNING: {model_config.display_model_version}')
    platform = U.discover_platform()
    exec(open(os.path.join(U.ctoaster_root, 'platforms', platform)).read())
    if 'runtime_env' in locals():
        for k, v in locals()['runtime_env'].items():
            os.environ[k] = v
    with open('run.log', 'w') as logfp:
        tstart = dt.datetime.now()
        manage(os.path.join('.', exe_name), logfp, run2, cont)

def run2(result, cont):
    global tstart
    if result == 0:
        d = (dt.datetime.now() - tstart).total_seconds()
        message('Run OK!  [elapsed: %02dh %02dm %02ds]' %
                (int(d / 3600), int(d / 60) % 60, int(d % 60)))
    else:
        message('RUN FAILED: see run.log for details')
    if cont: cont()


# Actions: platform management, clean, build or run.

pfile = os.path.join('config', 'platform-name')
if gui:
    root = tk.Tk()
    app = Application(root)
    message = app.message
    line = app.line
    manage = app.manage
    root.mainloop()
else:
    message = console_message
    line = console_line
    manage = console_manage
    # Non-GUI command execution
    if args.command == 'clear-platform':
        if os.path.exists(pfile): os.remove(pfile)
    elif args.command == 'set-platform':
        with open(pfile, 'w') as ofp: print(args.platform, file=ofp)
    elif args.command == 'clean':
        clean(False)
    elif args.command == 'cleaner':
        clean(True)
    elif args.command == 'clean-build':
        clean_build()
    elif args.command == 'build':
        # Assuming build function can handle None or specific build type and progress flag
        build(None)
    elif args.command == 'run':
        # For the 'run' command, ensuring build function is called appropriately
        build(run)  # Adjusted to match the original functionality
