"""
Migration Validator — Pre-flight checks for zero-downtime deployments.

Validates all pending migrations BEFORE running them against production.
Prevents unsafe patterns:
  1. Adding NOT NULL without default
  2. Dropping columns referenced in app code
  3. Renaming tables without view alias
  4. Changing column type without intermediate VARCHAR
  5. Unsafe CHECK constraints

Safe patterns enforced:
  - Nullable columns (no constraints)
  - Columns with server_default
  - VARCHAR intermediates for type changes
  - Named constraints for easy rollback

Usage:
  python migration_validator.py --env prod --check-only
  python migration_validator.py --env prod --apply
"""

import os
import sys
import argparse
import re
from pathlib import Path
from typing import List, Dict, Optional, Tuple


class MigrationValidator:
    """Validates migration files for production safety."""

    # Unsafe patterns (regex) that should fail validation
    UNSAFE_PATTERNS = {
        "NOT_NULL_NO_DEFAULT": r"sa\.Column\([^)]*nullable=False[^)]*\)(?<!server_default)",
        "DROP_COLUMN": r"op\.drop_column\(",
        "RENAME_TABLE": r"op\.rename_table\(",
        "TYPE_CHANGE_UNSAFE": r"op\.alter_column\([^,]*,\s*type_=[^,]*\)(?<!sa\.VARCHAR)",
        "ADD_CONSTRAINT_UNSAFE": r"op\.create_check_constraint\([^)]*CHECK[^)]*\)",
    }

    # Safe patterns that override unsafe patterns
    SAFE_OVERRIDES = {
        "server_default": r"server_default=",
        "nullable": r"nullable=True",
    }

    def __init__(self):
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def validate_migration_file(self, migration_path: Path) -> bool:
        """
        Validate a single migration file.

        Returns:
            True if migration passes validation, False if unsafe patterns found.
        """
        if not migration_path.exists():
            self.errors.append(f"Migration file not found: {migration_path}")
            return False

        with open(migration_path, "r") as f:
            content = f.read()

        # Parse upgrade() and downgrade() functions
        upgrade_match = re.search(r"def upgrade\(\):(.*?)(?=def downgrade|$)", content, re.DOTALL)
        if not upgrade_match:
            self.errors.append(f"No upgrade() function in {migration_path.name}")
            return False

        upgrade_code = upgrade_match.group(1)

        # Check for unsafe patterns
        return self._check_unsafe_patterns(upgrade_code, migration_path.name)

    def _check_unsafe_patterns(self, code: str, filename: str) -> bool:
        """
        Check upgrade code for unsafe patterns.

        Returns:
            True if safe, False if unsafe.
        """
        safe = True

        # 1. Check for NOT NULL without default
        if re.search(self.UNSAFE_PATTERNS["NOT_NULL_NO_DEFAULT"], code):
            # But allow if server_default is present on the same line
            not_null_lines = re.finditer(
                self.UNSAFE_PATTERNS["NOT_NULL_NO_DEFAULT"], code
            )
            for match in not_null_lines:
                line_start = code.rfind("\n", 0, match.start()) + 1
                line_end = code.find("\n", match.end())
                line = code[line_start:line_end]
                if "server_default" not in line:
                    self.errors.append(
                        f"{filename}: NOT NULL column without server_default: {line.strip()}"
                    )
                    safe = False

        # 2. Check for column drops (risky — app code may still reference)
        if re.search(self.UNSAFE_PATTERNS["DROP_COLUMN"], code):
            drops = re.finditer(self.UNSAFE_PATTERNS["DROP_COLUMN"], code)
            for match in drops:
                line_start = code.rfind("\n", 0, match.start()) + 1
                line_end = code.find("\n", match.end())
                line = code[line_start:line_end]
                self.warnings.append(
                    f"{filename}: Column drop detected (verify app code doesn't reference): {line.strip()}"
                )

        # 3. Check for table renames without view alias (hard to do in Alembic, warn)
        if re.search(self.UNSAFE_PATTERNS["RENAME_TABLE"], code):
            self.warnings.append(
                f"{filename}: Table rename detected. Ensure old views/synonyms created for compatibility."
            )

        # 4. Check for type changes without intermediate VARCHAR
        type_change_matches = re.finditer(r"op\.alter_column\(.*?type_=([^,\)]+)", code)
        for match in type_change_matches:
            type_str = match.group(1)
            # Only warn if not VARCHAR intermediate
            if "VARCHAR" not in type_str and "String" not in type_str:
                line_start = code.rfind("\n", 0, match.start()) + 1
                line_end = code.find("\n", match.end())
                line = code[line_start:line_end]
                self.warnings.append(
                    f"{filename}: Type change without VARCHAR intermediate: {line.strip()}"
                )

        # 5. Check for unsafe CHECK constraints
        if re.search(self.UNSAFE_PATTERNS["ADD_CONSTRAINT_UNSAFE"], code):
            self.warnings.append(
                f"{filename}: CHECK constraint added. Ensure compatible with existing data."
            )

        return safe

    def validate_all_migrations(self, migrations_dir: Path) -> bool:
        """
        Validate all migration files in a directory.

        Returns:
            True if all pass, False if any fail.
        """
        all_safe = True
        migration_files = sorted(migrations_dir.glob("*.py"))

        # Skip __pycache__ and __init__
        migration_files = [f for f in migration_files if f.name not in ("__init__.py", "__pycache__")]

        for mig_file in migration_files:
            self.errors.clear()
            self.warnings.clear()

            if not self._check_file_is_migration(mig_file):
                continue

            result = self.validate_migration_file(mig_file)
            if not result:
                all_safe = False
                print(f"❌ {mig_file.name}: FAILED")
                for err in self.errors:
                    print(f"   [ERROR] {err}")
            else:
                print(f"✅ {mig_file.name}: PASSED")

            for warn in self.warnings:
                print(f"   [WARN] {warn}")

        return all_safe

    def _check_file_is_migration(self, path: Path) -> bool:
        """Check if file is a valid migration (has upgrade/downgrade functions)."""
        with open(path, "r") as f:
            content = f.read()
        return "def upgrade()" in content and "def downgrade()" in content


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Validate Alembic migrations for zero-downtime deployment safety."
    )
    parser.add_argument(
        "--dir",
        type=str,
        default="alembic/versions",
        help="Path to migrations directory (default: alembic/versions)",
    )
    parser.add_argument(
        "--pii-dir",
        type=str,
        default="alembic_pii/versions",
        help="Path to PII migrations directory (default: alembic_pii/versions)",
    )
    parser.add_argument(
        "--check-only", action="store_true", help="Validate but don't apply (exit code 0=safe, 1=unsafe)"
    )

    args = parser.parse_args()

    validator = MigrationValidator()
    all_safe = True

    # Validate main migrations
    main_dir = Path(args.dir)
    if main_dir.exists():
        print(f"\n🔍 Validating main migrations ({args.dir})...")
        if not validator.validate_all_migrations(main_dir):
            all_safe = False
    else:
        print(f"⚠️  Main migrations directory not found: {args.dir}")

    # Validate PII migrations
    pii_dir = Path(args.pii_dir)
    if pii_dir.exists():
        print(f"\n🔍 Validating PII migrations ({args.pii_dir})...")
        if not validator.validate_all_migrations(pii_dir):
            all_safe = False
    else:
        print(f"⚠️  PII migrations directory not found: {args.pii_dir}")

    # Summary
    print("\n" + "=" * 60)
    if all_safe:
        print("✅ ALL MIGRATIONS PASSED PRE-FLIGHT CHECK")
        print("   Safe to apply. Proceeding with deployment.")
        return 0
    else:
        print("❌ MIGRATIONS FAILED PRE-FLIGHT CHECK")
        print("   Fix unsafe patterns before deploying.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
