import base64
import copy
import copy
import os
import time

from django.utils.decorators import method_decorator
from django.views.decorators.gzip import gzip_page
from rest_framework import mixins
from rest_framework.decorators import action

from applications.task.constants import ALLOW_TYPE
from applications.task.filters import TaskFilters
from applications.task.models import TaskRecord, Task
from applications.task.serialziers import FileListSerializer, Id3Serializer, UpdateId3Serializer, \
    FetchId3ByTitleSerializer, FetchLlyricSerializer, BatchUpdateId3Serializer, TranslationLycSerializer, \
    TidyFolderSerializer, TaskSerializer, UploadImageSerializer
from applications.task.services.music_ids import MusicIDS
from applications.task.services.music_resource import MusicResource
from applications.task.services.update_ids import update_music_info
from applications.task.tasks import full_scan_folder, scan, clear_music, batch_auto_tag_task, tidy_folder_task
from applications.utils.translation import translation_lyc_text
from component.drf.viewsets import GenericViewSet
from django_vue_cli.celery_app import app as celery_app


@method_decorator(gzip_page, name="dispatch")
class TaskViewSets(GenericViewSet):
    def get_serializer_class(self):
        if self.action == "file_list":
            return FileListSerializer
        elif self.action == "music_id3":
            return Id3Serializer
        elif self.action == "update_id3":
            return UpdateId3Serializer
        elif self.action == "fetch_id3_by_title":
            return FetchId3ByTitleSerializer
        elif self.action == "fetch_lyric":
            return FetchLlyricSerializer
        elif self.action in ["batch_update_id3", "batch_auto_update_id3"]:
            return BatchUpdateId3Serializer
        elif self.action == "translation_lyc":
            return TranslationLycSerializer
        elif self.action == "tidy_folder":
            return TidyFolderSerializer
        elif self.action == "upload_image":
            return UploadImageSerializer
        return FileListSerializer

    @action(methods=['POST'], detail=False)
    def file_list(self, request, *args, **kwargs):
        """文件列表"""
        validate_data = self.is_validated_data(request.data)
        file_path = validate_data['file_path']
        sorted_fields = validate_data['sorted_fields']
        file_path_list = file_path.split('/')
        try:
            data = os.scandir(file_path)
        except FileNotFoundError:
            return self.failure_response(msg="文件夹不存在")
        children_data = []
        frc_map = {}
        file_data = []
        full_path_list = []
        for entry in data:
            each = entry.name.encode('utf-8', 'replace').decode()
            file_data.append({
                "name": each,
                "path": entry.path.encode('utf-8', 'replace').decode(),
                "is_dir": entry.is_dir(),
                "update_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(entry.stat().st_mtime)),
                "size": entry.stat().st_size
            })
            full_path_list.append(f"{file_path}/{each}")
            file_type = each.split(".")[-1]
            file_name = ".".join(each.split(".")[:-1])
            if file_type in ["lrc", "txt"]:
                frc_map[file_name] = each
        task_map = dict(Task.objects.filter(parent_path=file_path).values_list("filename", "state"))
        for index, entry in enumerate(file_data, 1):
            each = entry.get("name")
            file_type = each.split(".")[-1]
            file_name = ".".join(each.split(".")[:-1])
            if entry.get("is_dir", None):
                children_data.append({
                    "id": index,
                    "name": each,
                    "title": each,
                    "icon": "icon-folder",
                    "state": "null",
                    "children": [],
                    "size": entry.get("size"),
                    "update_time": entry.get("update_time")
                })
                continue
            if file_type not in ALLOW_TYPE:
                continue
            if file_name in frc_map:
                icon = "icon-script-files"
            else:
                icon = "icon-script-file"
            children_data.append({
                "id": index,
                "name": each,
                "title": each,
                "icon": icon,
                "state": task_map.get(each, "null"),
                "size": entry.get("size"),
                "update_time": entry.get("update_time")
            })
        if "name" in sorted_fields:
            children_data = sorted(children_data, key=lambda x: x.get("name").encode('gbk', "ignore"), reverse=False)
        if "update_time" in sorted_fields:
            children_data = sorted(children_data, key=lambda x: x.get("update_time"), reverse=True)
        if "size" in sorted_fields:
            children_data = sorted(children_data, key=lambda x: x.get("size"), reverse=True)
        children_data = sorted(children_data, key=lambda x: 'children' not in x)
        res_data = [
            {
                "name": file_path_list[-1],
                "title": file_path_list[-1],
                "expanded": True,
                "id": 0,
                "children": children_data,
                "icon": "icon-folder",
            }
        ]
        return self.success_response(data=res_data)

    @action(methods=['POST'], detail=False)
    def music_id3(self, request, *args, **kwargs):
        """获取音乐id3信息"""
        validate_data = self.is_validated_data(request.data)
        file_path = validate_data['file_path']
        file_name = validate_data['file_name']
        file_type = file_name.split(".")[-1]
        if file_type in ["lrc", "txt"]:
            return self.success_response()
        file_path = file_path.rstrip('/')
        sub_path = file_path.split('/')[-1]
        if sub_path == file_name:
            return self.success_response()
        try:
            res_data = MusicIDS(f"{file_path}/{file_name}").to_dict()
        except Exception as e:
            return self.failure_response(msg=str(e))
        return self.success_response(data=res_data)

    @action(methods=['POST'], detail=False)
    def update_id3(self, request, *args, **kwargs):
        """更新音乐id3信息"""
        validate_data = self.is_validated_data(request.data)
        music_id3_info = validate_data['music_id3_info']
        update_music_info(music_id3_info, False)
        return self.success_response()

    @action(methods=['POST'], detail=False)
    def batch_update_id3(self, request, *args, **kwargs):
        """批量更新音乐id3信息"""
        validate_data = self.is_validated_data(request.data)
        full_path = validate_data['file_full_path']
        select_data = validate_data['select_data']
        music_info = validate_data['music_info']
        music_id3_info = []
        for data in select_data:
            if data.get('icon') == 'icon-folder':
                file_full_path = f"{full_path}/{data.get('name')}"
                data = os.scandir(file_full_path)
                allow_type = ["flac", "mp3", "ape", "wav", "aiff", "wv", "tta", "m4a", "ogg", "mpc",
                              "opus", "wma", "dsf", "dff"]
                for index, entry in enumerate(data, 1):
                    each = entry.name
                    file_type = each.split(".")[-1]
                    if file_type not in allow_type:
                        continue
                    music_info.update({
                        "file_full_path": f"{file_full_path}/{each}",
                        "filename": each
                    })
                    music_id3_info.append(copy.deepcopy(music_info))
            else:
                music_info.update({
                    "file_full_path": f"{full_path}/{data.get('name')}",
                })
                music_id3_info.append(copy.deepcopy(music_info))
        update_music_info(music_id3_info, False)
        return self.success_response()

    @action(methods=['POST'], detail=False)
    def batch_auto_update_id3(self, request, *args, **kwargs):
        validate_data = self.is_validated_data(request.data)
        full_path = validate_data['file_full_path']
        select_data = validate_data['select_data']
        music_info = validate_data['music_info']
        select_mode = music_info["select_mode"]
        source_list = music_info.get("source_list", [])
        timestamp = str(int(time.time() * 1000))
        bulk_set = []
        for each in select_data:
            name = each.get("name")
            song_name = ".".join(name.split(".")[:-1])
            bulk_set.append(TaskRecord(**{
                "song_name": song_name,
                "full_path": f"{full_path}/{name}",
                "icon": each.get("icon"),
                "batch": timestamp
            }))
        TaskRecord.objects.bulk_create(bulk_set, batch_size=500)
        batch_auto_tag_task(timestamp, source_list, select_mode)
        return self.success_response()

    @action(methods=['POST'], detail=False)
    def fetch_lyric(self, request, *args, **kwargs):
        validate_data = self.is_validated_data(request.data)
        resource = validate_data["resource"]
        song_id = validate_data["song_id"]
        try:
            lyric = MusicResource(resource).fetch_lyric(song_id) or ""
        except Exception as e:
            lyric = f"未找到歌词 {e}"
        return self.success_response(data=lyric)

    @action(methods=['POST'], detail=False)
    def fetch_id3_by_title(self, request, *args, **kwargs):
        validate_data = self.is_validated_data(request.data)
        resource = validate_data["resource"]
        full_path = validate_data.get("full_path", "")
        title = validate_data["title"]

        if resource == "acoustid":
            title = full_path
        elif resource == "smart_tag":
            title = {"title": title, "full_path": full_path}
        songs = MusicResource(resource).fetch_id3_by_title(title)
        return self.success_response(data=songs)

    @action(methods=['POST'], detail=False)
    def translation_lyc(self, request, *args, **kwargs):
        validate_data = self.is_validated_data(request.data)
        lyc = validate_data["lyc"]
        clean_lyc_list = []
        raw_lyc_list = []
        for line in lyc.split("\n"):
            if not line:
                continue
            clean_line = line.split("]")[-1]
            clean_line = clean_line.strip()
            if not clean_line:
                continue
            raw_lyc_list.append(line)
            clean_lyc_list.append(clean_line)
        clean_lyc_str = "\n".join(clean_lyc_list)
        results = translation_lyc_text(clean_lyc_str)
        new_lyc = []
        results_list = results.split("\n")
        for index, result in enumerate(results_list):
            if not result:
                new_lyc.append(raw_lyc_list[index])
            else:
                try:
                    src = clean_lyc_list[index]
                    raw_src = raw_lyc_list[index]
                except Exception as e:
                    continue
                if src.replace(" ", "") == result.replace(" ", ""):
                    new_lyc.append(raw_src)
                else:
                    new_lyc.append(f"{raw_src}\n「{result}」\n")
        return self.success_response(data="\n".join(new_lyc))

    @action(methods=['POST'], detail=False)
    def tidy_folder(self, request, *args, **kwargs):
        validate_data = self.is_validated_data(request.data)
        root_path = validate_data["root_path"]
        first_dir = validate_data["first_dir"]
        full_path = validate_data["file_full_path"]
        select_data = validate_data["select_data"]
        second_dir = validate_data.get("second_dir", "")
        music_id3_info = []
        for data in select_data:
            if data.get('icon') == 'icon-folder':
                file_full_path = f"{full_path}/{data.get('name')}"
                data = os.scandir(file_full_path)
                for index, entry in enumerate(data, 1):
                    each = entry.name
                    file_type = each.split(".")[-1]
                    if file_type not in ALLOW_TYPE:
                        continue
                    music_id3_info.append(f"{file_full_path}/{each}")
            else:
                music_id3_info.append(f"{full_path}/{data.get('name')}")
        tidy_folder_task(music_id3_info, {"root_path": root_path, "first_dir": first_dir, "second_dir": second_dir})
        return self.success_response()

    @action(methods=['POST'], detail=False)
    def upload_image(self, request, *args, **kwargs):
        upload_file = request.FILES.get('upload_file')

        bs64_img = base64.b64encode(upload_file.read()).decode()
        # bs64_img_str = "data:image/jpeg;base64," + bs64_img
        return self.success_response(data=bs64_img)

    @action(methods=["get"], detail=False)
    def clear_celery(self, request, *args, **kwargs):
        active_tasks = celery_app.control.inspect().active()
        try:
            active_tasks_data = list(active_tasks.values())[0]
        except Exception:
            return self.success_response()
        for task in active_tasks_data:
            celery_app.control.revoke(task["id"], terminate=True)
        celery_app.control.purge()
        return self.success_response()

    @action(methods=["get"], detail=False)
    def active_queue(self, request, *args, **kwargs):
        active_tasks = celery_app.control.inspect().active()
        try:
            active_tasks_data = list(active_tasks.values())[0]
        except Exception:
            return self.success_response()
        return self.success_response(data=active_tasks_data)

    @action(methods=['GET'], detail=False)
    def task1(self, request, *args, **kwargs):
        scan.delay()
        return self.success_response()

    @action(methods=['GET'], detail=False)
    def task2(self, request, *args, **kwargs):
        clear_music()
        return self.success_response()

    @action(methods=['GET'], detail=False)
    def full_scan_folder(self, request, *args, **kwargs):
        full_scan_folder.delay()
        return self.success_response()


class TaskModelViewSets(mixins.ListModelMixin,
                        GenericViewSet):
    queryset = Task.objects.order_by("-id")
    serializer_class = TaskSerializer
    filterset_class = TaskFilters
