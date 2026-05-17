"""
Refactoring Agent Module

This module provides an automated refactoring agent for Python repositories.
It scans the repository, generates refactoring plans in batches, and applies patches
while maintaining the original behavior. Includes advanced features like caching,
detailed logging, error handling, and final reporting.

Usage:
    python agent.py

Dependencies:
    - repo_scanner: For scanning Python files.
    - refactor_engine: For generating refactoring plans.
    - patcher: For applying patches.
"""

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Dict, List
from repo_scanner import scan_repository
from refactor_engine import generate_refactor_plan
from patcher import apply_patches


class RefactoringError(Exception):
    """Custom exception for refactoring-related errors."""
    pass


class RefactoringAgent:
    """
    Agent responsible for orchestrating the refactoring process of a Python repository.

    This class handles scanning files, processing them in batches, and applying refactoring patches.
    It ensures error handling and logging throughout the process.
    """

    def __init__(self, repo_path: str, batch_size: int = 10, max_retries: int = 3):
        """
        Initialize the RefactoringAgent.

        Args:
            repo_path (str): Path to the repository root directory.
            batch_size (int): Number of files to process per batch. Defaults to 10.
            max_retries (int): Maximum retries for failed batches. Defaults to 3.
        """
        self.repo_path = Path(repo_path).resolve()
        self.batch_size = batch_size
        self.max_retries = max_retries
        self.stats = {
            "files_scanned": 0,
            "files_processed": 0,
            "batches_processed": 0,
            "batches_failed": 0,
            "errors": []
        }
        if not self.repo_path.exists() or not self.repo_path.is_dir():
            raise ValueError(f"Repository path {self.repo_path} does not exist or is not a directory.")

    def scan_repository(self, cache: Dict[str, str]) -> List[Dict]:
        """
        Scan the repository for Python files, skipping cached ones.

        Args:
            cache (Dict[str, str]): Cache of file hashes.

        Returns:
            List[Dict]: List of dictionaries containing file paths and contents.

        Raises:
            RefactoringError: If scanning fails.
        """
        try:
            logger.info(f"Scanning repository: {self.repo_path}")
            all_files = scan_repository(str(self.repo_path))
            files_to_process = []
            for file_info in all_files:
                file_path = file_info["path"]
                content = file_info["content"]
                file_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()
                if cache.get(file_path) == file_hash:
                    logger.info(f"Skipping cached file: {file_path}")
                    continue
                files_to_process.append(file_info)
                cache[file_path] = file_hash  # Update cache
            self.stats["files_scanned"] = len(all_files)
            self.stats["files_to_process"] = len(files_to_process)
            logger.info(f"Found {len(all_files)} Python files, {len(files_to_process)} to process.")
            return files_to_process
        except Exception as e:
            logger.error(f"Error scanning repository: {e}")
            raise RefactoringError("Failed to scan repository.") from e

    def process_batches(self, files: List[Dict]) -> None:
        """
        Process files in batches: generate plans and apply patches with retries.

        Args:
            files (List[Dict]): List of files to process.

        Raises:
            RefactoringError: If too many batches fail.
        """
        for i in range(0, len(files), self.batch_size):
            batch = files[i:i + self.batch_size]
            batch_number = i // self.batch_size + 1
            logger.info(f"Processing batch {batch_number} with {len(batch)} files.")

            for attempt in range(self.max_retries):
                try:
                    plan_json = generate_refactor_plan(batch)
                    if not plan_json:
                        logger.warning("No refactoring plan generated for this batch. Skipping.")
                        break

                    self.apply_patches_to_batch(plan_json)
                    self.stats["batches_processed"] += 1
                    self.stats["files_processed"] += len(batch)
                    logger.info("Batch completed successfully.")
                    break  # Success, exit retry loop
                except Exception as e:
                    logger.warning(f"Attempt {attempt + 1} failed for batch {batch_number}: {e}")
                    self.stats["errors"].append(f"Batch {batch_number}, attempt {attempt + 1}: {str(e)}")
                    if attempt == self.max_retries - 1:
                        self.stats["batches_failed"] += 1
                        logger.error(f"Batch {batch_number} failed after {self.max_retries} attempts.")
                        # Continue to next batch instead of stopping
            else:
                raise RefactoringError(f"Batch {batch_number} failed permanently.")

    def apply_patches_to_batch(self, plan_json: str) -> None:
        """
        Apply patches for a single batch.

        Args:
            plan_json (str): JSON string containing the refactoring plan.

        Raises:
            RefactoringError: If applying patches fails.
        """
        try:
            apply_patches(plan_json)
        except Exception as e:
            logger.error(f"Error applying patches: {e}")
            raise RefactoringError("Failed to apply patches.") from e

    def generate_report(self) -> str:
        """
        Generate a final statistics report.

        Returns:
            str: Formatted report string.
        """
        report = f"""
Refactoring Report
==================
Repository: {self.repo_path}
Files Scanned: {self.stats['files_scanned']}
Files Processed: {self.stats['files_processed']}
Batches Processed: {self.stats['batches_processed']}
Batches Failed: {self.stats['batches_failed']}
Errors Encountered: {len(self.stats['errors'])}
"""
        if self.stats['errors']:
            report += "\nErrors:\n" + "\n".join(f"- {err}" for err in self.stats['errors'])
        return report


class RefactoringOrchestrator:
    """
    Orchestrator for the refactoring process, handling initialization, caching, logging, and reporting.
    """

    def __init__(self, repo_path: str, batch_size: int = 10, max_retries: int = 3, log_file: str = "refactoring.log"):
        """
        Initialize the Orchestrator.

        Args:
            repo_path (str): Path to the repository.
            batch_size (int): Batch size for processing.
            max_retries (int): Max retries for batches.
            log_file (str): Path to the log file.
        """
        self.repo_path = repo_path
        self.batch_size = batch_size
        self.max_retries = max_retries
        self.log_file = Path(repo_path) / log_file
        self.cache_file = Path(repo_path) / "refactoring_cache.json"
        self.cache = self.load_cache()
        self.setup_logging()

    def setup_logging(self) -> None:
        """Set up logging with file handler."""
        logger = logging.getLogger()
        logger.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        # File handler
        file_handler = logging.FileHandler(self.log_file)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    def load_cache(self) -> Dict[str, str]:
        """Load the processing cache from file."""
        if self.cache_file.exists():
            try:
                with open(self.cache_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load cache: {e}")
        return {}

    def save_cache(self) -> None:
        """Save the processing cache to file."""
        try:
            with open(self.cache_file, 'w') as f:
                json.dump(self.cache, f, indent=4)
            logger.info("Cache saved.")
        except Exception as e:
            logger.error(f"Failed to save cache: {e}")

    def run(self) -> None:
        """Run the full refactoring workflow."""
        logger.info("Starting refactoring orchestration.")
        try:
            agent = RefactoringAgent(self.repo_path, self.batch_size, self.max_retries)
            files = agent.scan_repository(self.cache)
            agent.process_batches(files)
            report = agent.generate_report()
            logger.info("Refactoring completed.")
            print(report)  # Print report to console
            self.save_cache()
        except RefactoringError as e:
            logger.error(f"Refactoring failed: {e}")
            raise
        except Exception as e:
            logger.critical(f"Unexpected error: {e}")
            raise


def main():
    """
    Main entry point for the refactoring orchestrator.
    """
    repo_path = os.getcwd()
    orchestrator = RefactoringOrchestrator(repo_path)
    orchestrator.run()


if __name__ == "__main__":
    main()
