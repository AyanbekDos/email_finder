import os
import difflib
from pathlib import Path
from datetime import datetime
import glob
import sys
import json

def get_next_version_number(snapshots_dir):
    """Получает следующий номер версии"""
    if not snapshots_dir.exists():
        return 1
    
    existing_files = list(snapshots_dir.glob("v*.txt"))
    if not existing_files:
        return 1
    
    # Извлекаем номера версий
    version_numbers = []
    for file in existing_files:
        try:
            # Извлекаем номер из имени файла v001.txt -> 1
            version_str = file.stem[1:]  # убираем 'v'
            version_numbers.append(int(version_str))
        except ValueError:
            continue
    
    return max(version_numbers) + 1 if version_numbers else 1

def analyze_differences(old_content, new_content):
    """Анализирует различия между двумя версиями без ИИ"""
    if not old_content:
        return {
            "summary": "Первая версия проекта",
            "files_added": 0,
            "files_removed": 0,
            "files_modified": 0,
            "total_lines_added": 0,
            "total_lines_removed": 0,
            "details": []
        }
    
    old_lines = old_content.split('\n')
    new_lines = new_content.split('\n')
    
    # Простой анализ изменений
    diff = list(difflib.unified_diff(old_lines, new_lines, lineterm=''))
    
    lines_added = sum(1 for line in diff if line.startswith('+') and not line.startswith('+++'))
    lines_removed = sum(1 for line in diff if line.startswith('-') and not line.startswith('---'))
    
    # Анализ файлов (ищем заголовки файлов в содержимом)
    old_files = extract_file_list(old_content)
    new_files = extract_file_list(new_content)
    
    files_added = len(new_files - old_files)
    files_removed = len(old_files - new_files)
    files_modified = len(old_files & new_files)  # Пересечение
    
    # Детальный анализ изменений
    details = []
    
    if files_added > 0:
        added_files = list(new_files - old_files)[:5]  # Показываем первые 5
        details.append(f"Добавлены файлы: {', '.join(added_files)}")
    
    if files_removed > 0:
        removed_files = list(old_files - new_files)[:5]
        details.append(f"Удалены файлы: {', '.join(removed_files)}")
    
    # Определяем тип изменений
    if lines_added > lines_removed * 2:
        change_type = "Крупное расширение функциональности"
    elif lines_removed > lines_added * 2:
        change_type = "Значительное сокращение кода"
    elif lines_added > 50 or lines_removed > 50:
        change_type = "Существенные изменения"
    elif lines_added > 10 or lines_removed > 10:
        change_type = "Умеренные изменения"
    else:
        change_type = "Минорные изменения"
    
    return {
        "summary": change_type,
        "files_added": files_added,
        "files_removed": files_removed,
        "files_modified": files_modified,
        "total_lines_added": lines_added,
        "total_lines_removed": lines_removed,
        "details": details
    }

def extract_file_list(content):
    """Извлекает список файлов из содержимого"""
    files = set()
    lines = content.split('\n')
    
    for line in lines:
        # Ищем заголовки файлов в формате "# 1. ФАЙЛ: path/to/file.py"
        if line.startswith('# ') and 'ФАЙЛ:' in line:
            try:
                file_path = line.split('ФАЙЛ:')[1].strip()
                files.add(file_path)
            except IndexError:
                continue
    
    return files

def create_versioned_backup(root_dir, exclude_dirs=None):
    """
    Создает версионированный бэкап проекта с анализом изменений
    """
    
    if exclude_dirs is None:
        exclude_dirs = ['.git', '__pycache__', '.venv', 'venv', 'env', 
                       'node_modules', '.pytest_cache', '.mypy_cache', 'project_snapshots']
    
    root_path = Path(root_dir).resolve()
    snapshots_dir = root_path / "project_snapshots"
    snapshots_dir.mkdir(exist_ok=True)
    
    # Получаем номер следующей версии
    version_number = get_next_version_number(snapshots_dir)
    version_filename = f"v{version_number:03d}.txt"
    
    # Собираем все .py файлы
    python_files = []
    for file_path in glob.glob(str(root_path / "**/*.py"), recursive=True):
        file_path = Path(file_path)
        if not any(excluded in str(file_path) for excluded in exclude_dirs):
            if '__pycache__' not in str(file_path):
                python_files.append(file_path)
    
    python_files.sort()
    
    # Читаем предыдущую версию для сравнения
    previous_content = ""
    if version_number > 1:
        previous_file = snapshots_dir / f"v{version_number-1:03d}.txt"
        if previous_file.exists():
            try:
                with open(previous_file, 'r', encoding='utf-8') as f:
                    previous_content = f.read()
            except Exception:
                pass
    
    # Создаем текущий снимок
    current_content = generate_project_content(root_path, python_files)
    
    # Анализируем изменения
    diff_analysis = analyze_differences(previous_content, current_content)
    
    # Создаем финальный файл с анализом
    output_path = snapshots_dir / version_filename
    
    with open(output_path, 'w', encoding='utf-8') as outfile:
        # Заголовок с анализом изменений
        outfile.write(f"# СНИМОК ПРОЕКТА - ВЕРСИЯ {version_number:03d}\n")
        outfile.write(f"# Создан: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        outfile.write(f"# Корневая папка: {root_path}\n")
        outfile.write(f"# Всего файлов: {len(python_files)}\n")
        outfile.write("=" * 80 + "\n\n")
        
        # АНАЛИЗ ИЗМЕНЕНИЙ
        if version_number > 1:
            outfile.write("# АНАЛИЗ ИЗМЕНЕНИЙ С ПРЕДЫДУЩЕЙ ВЕРСИИ\n")
            outfile.write("=" * 50 + "\n")
            outfile.write(f"Тип изменений: {diff_analysis['summary']}\n")
            outfile.write(f"Файлов добавлено: {diff_analysis['files_added']}\n")
            outfile.write(f"Файлов удалено: {diff_analysis['files_removed']}\n")
            outfile.write(f"Файлов изменено: {diff_analysis['files_modified']}\n")
            outfile.write(f"Строк добавлено: +{diff_analysis['total_lines_added']}\n")
            outfile.write(f"Строк удалено: -{diff_analysis['total_lines_removed']}\n")
            
            if diff_analysis['details']:
                outfile.write(f"\nДетали изменений:\n")
                for detail in diff_analysis['details']:
                    outfile.write(f"• {detail}\n")
            
            outfile.write("\n" + "=" * 80 + "\n\n")
        
        # ОГЛАВЛЕНИЕ
        outfile.write("# ОГЛАВЛЕНИЕ ФАЙЛОВ\n")
        outfile.write("=" * 40 + "\n")
        
        for i, file_path in enumerate(python_files, 1):
            relative_path = file_path.relative_to(root_path)
            outfile.write(f"{i:3d}. {relative_path}\n")
        
        outfile.write("\n" + "=" * 80 + "\n\n")
        
        # СОДЕРЖИМОЕ ФАЙЛОВ
        outfile.write(current_content)
        
        # МЕТАДАННЫЕ
        outfile.write(f"\n{'#' * 80}\n")
        outfile.write(f"# МЕТАДАННЫЕ ВЕРСИИ\n")
        outfile.write(f"{'#' * 80}\n")
        outfile.write(f"# Версия: {version_number:03d}\n")

        # ✅ ИСПРАВЛЕНО: Выносим тернарный оператор из f-строки
        prev_version = f"{version_number-1:03d}" if version_number > 1 else "нет"
        outfile.write(f"# Предыдущая версия: {prev_version}\n")

        outfile.write(f"# Время создания: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        outfile.write(f"# Система: {sys.platform}\n")
        outfile.write(f"# Исключенные папки: {', '.join(exclude_dirs)}\n")
    
    print(f"✅ Снимок проекта создан!")
    print(f"📁 Версия: v{version_number:03d}")
    print(f"📄 Файл: {output_path}")
    print(f"🔍 Обработано файлов: {len(python_files)}")
    
    if version_number > 1:
        print(f"📊 Изменения: {diff_analysis['summary']}")
        print(f"   +{diff_analysis['total_lines_added']} строк, -{diff_analysis['total_lines_removed']} строк")
    
    return output_path, version_number

def generate_project_content(root_path, python_files):
    """Генерирует содержимое проекта"""
    content = ""
    
    for i, file_path in enumerate(python_files, 1):
        relative_path = file_path.relative_to(root_path)
        
        # Заголовок файла
        content += f"\n{'#' * 80}\n"
        content += f"# {i:3d}. ФАЙЛ: {relative_path}\n"
        content += f"# Полный путь: {file_path}\n"
        content += f"# Размер: {file_path.stat().st_size} байт\n"
        content += f"{'#' * 80}\n\n"
        
        try:
            # Пробуем разные кодировки
            encodings = ['utf-8', 'cp1251', 'latin1']
            file_content = None
            
            for encoding in encodings:
                try:
                    with open(file_path, 'r', encoding=encoding) as infile:
                        file_content = infile.read()
                        break
                except UnicodeDecodeError:
                    continue
            
            if file_content is None:
                content += f"# ОШИБКА: Не удалось прочитать файл ни в одной кодировке\n\n"
                continue
            
            if not file_content.strip():
                content += "# ФАЙЛ ПУСТОЙ\n\n"
            else:
                content += file_content
                if not file_content.endswith('\n'):
                    content += '\n'
                content += '\n'
                
        except Exception as e:
            content += f"# ОШИБКА ЧТЕНИЯ ФАЙЛА: {str(e)}\n\n"
    
    return content

def rollback_to_version(root_dir, target_version):
    """Показывает содержимое определенной версии (для анализа)"""
    root_path = Path(root_dir).resolve()
    snapshots_dir = root_path / "project_snapshots"
    
    version_file = snapshots_dir / f"v{target_version:03d}.txt"
    
    if not version_file.exists():
        print(f"❌ Версия v{target_version:03d} не найдена!")
        return None
    
    print(f"📖 Показываю версию v{target_version:03d}")
    print(f"📄 Файл: {version_file}")
    
    return version_file

# Примеры использования
if __name__ == "__main__":
    # Создать новый снимок проекта
    create_versioned_backup(".")
    
    # Посмотреть определенную версию
    # rollback_to_version(".", 2)
