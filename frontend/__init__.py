"""Giao diện và lớp điều phối của Translate Select.

Cách chia trách nhiệm
---------------------

``state.py`` — ``ApplicationState`` giữ trạng thái thuần của ứng dụng:
đang chạy hay đã dừng, chế độ xử lý (LLM, Dictionary hoặc cả hai), tài nguyên
Selection và các pipeline dịch được tạo khi cần. Lớp này không tạo hay cập
nhật widget.

``control_panel.py`` — ``ControlPanel`` là giao diện nút điều khiển. Nó phát
các signal như ``llm_requested``, ``dictionary_requested``, ``start_requested``
và ``stop_requested`` để báo ý định của người dùng; nó không tự thay đổi state
hoặc gọi backend.

``controller.py`` — ``Controller`` ghép các phần trên: tạo và giữ các cửa sổ,
kết nối signal từ ControlPanel, cập nhật ApplicationState, điều phối Selection
và gửi yêu cầu dịch tới backend. Ứng dụng nên tạo một Controller duy nhất và
để Controller quản lý vòng đời của các thành phần.

Cách khởi chạy ứng dụng
----------------------

``app.py`` tạo QApplication, tạo Controller, đăng ký ``shutdown`` khi Qt sắp
thoát, gọi ``start()`` rồi chạy event loop:

    from PyQt6.QtWidgets import QApplication
    from frontend import Controller

    app = QApplication(sys.argv)
    controller = Controller()
    app.aboutToQuit.connect(controller.shutdown)
    controller.start()
    raise SystemExit(app.exec())

Có thể truyền state đã cấu hình sẵn khi cần:

    from frontend import ApplicationState, Controller, ProcessingMode

    state = ApplicationState(mode=ProcessingMode.DICTIONARY)
    controller = Controller(state=state)

Trong ứng dụng thực tế, không tự tạo thêm ControlPanel hoặc tự kết nối các
signal của nó tới backend; Controller đã đảm nhiệm phần đó. ControlPanel có thể
được dùng độc lập trong preview/UI test, nhưng khi đó mã gọi phải tự kết nối
các signal và tự quản lý state.
"""

from .control_panel import ControlPanel
from .controller import Controller
from .state import ApplicationLifecycle, ApplicationState, ProcessingMode

__all__ = [
    "ApplicationLifecycle",
    "ApplicationState",
    "ControlPanel",
    "Controller",
    "ProcessingMode",
]
