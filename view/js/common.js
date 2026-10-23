// 모든 로그인 후 화면에서 쓰는 공통 함수

// 상단 바: 아이디 표시
async function initTopbar() {
    try {
        const { data } = await axios.get('/api/me');
        $('#who').text(`${data.id}님`);
    } catch (e) {
        // 401이면 api.js가 로그인 화면으로 보냄
    }
}

function logout() {
    sessionStorage.clear();
    location.href = '/';
}

// 로딩 스피너를 켠 채로 함수 실행
async function withLoading(fn) {
    $('#loading').css('display', 'flex');
    try {
        return await fn();
    } finally {
        $('#loading').hide();
    }
}

// 서버 에러 메시지 꺼내기 (422 목록형 에러도 처리)
function errorMessage(e, fallback = '요청에 실패했어요') {
    const detail = e.response?.data?.detail;
    if (Array.isArray(detail)) return detail.map(d => `${d.loc.at(-1)}: ${d.msg}`).join('\n');
    return detail || fallback;
}