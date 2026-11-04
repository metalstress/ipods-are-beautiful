"""Native application entry point."""
import sys

if '--backend-job' in sys.argv:
    from backend_worker import execute
    execute(sys.argv[sys.argv.index('--backend-job')+1])
else:
    from application import Window, Job, main
    if __name__ == '__main__':main()
