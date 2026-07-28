import argparse
import os
import subprocess
import sys


def run(cmd, check=True):
    print('>', ' '.join(cmd))
    subprocess.run(cmd, check=check)


def main():
    parser = argparse.ArgumentParser(description='Safely update VolCAD without purging customer data.')
    parser.add_argument('--customer', default=None, help='Path to customer JSON config to re-apply (e.g. customer.json).')
    parser.add_argument('--skip-backup', action='store_true', help='Skip the pre-update database backup (not recommended).')
    parser.add_argument('--skip-pull', action='store_true', help='Skip git pull.')
    parser.add_argument('--skip-migrate', action='store_true', help='Skip schema setup/migration.')
    parser.add_argument('--skip-customer', action='store_true', help='Skip customer setup even if --customer is provided.')
    args = parser.parse_args()

    if not args.skip_pull:
        run(['git', 'pull'])

    if not args.skip_backup:
        run([sys.executable, 'backup.py'])

    if not args.skip_migrate:
        # setup_db.py will also attempt a backup unless --no-backup is passed. We already backed up.
        run([sys.executable, 'setup_db.py', '--no-backup'])

    if args.customer and not args.skip_customer:
        customer_path = os.path.abspath(args.customer)
        if not os.path.exists(customer_path):
            print(f'Customer config not found: {customer_path}', file=sys.stderr)
            sys.exit(1)
        # setup_customer.py calls setup_db.py internally; avoid a second backup here.
        run([sys.executable, 'setup_customer.py', customer_path, '--no-backup'])

    print('\nUpdate complete.')
    print('Next: run tests if available, then restart the app (e.g. uvicorn app:app --reload).')


if __name__ == '__main__':
    main()
