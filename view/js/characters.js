// 픽셀 면접관 그리기
// 한 글자 = 한 칸. '.'은 빈칸, 나머지 글자는 PALETTE의 색.

const PALETTE = {
    B: '#6B3E26', K: '#24262D', G: '#A3A9B2',
    S: '#F2C7A0', s: '#DDA77F', E: '#1A1A1F', M: '#B4534A', P: '#F09A9A',
    L: '#30343B', C: '#3F9B6E', c: '#2E7A55', w: '#EDEFF2',
    N: '#2C3E66', n: '#1F2D4D', T: '#8892A6', D: '#2F2F36', d: '#1C1C21', R: '#C0392B',
};

const CHARACTERS = {
    GOOD: {
        name: '김다정 팀장',
        role: '착한 면접관',
        art: [
            '................',
            '.....BBBBBB.....',
            '....BBBBBBBB....',
            '...BBBBBBBBBB...',
            '...BBSSSSSSBB...',
            '...BSSSSSSSSB...',
            '...BSESSSSESB...',
            '...BSSSSSSSSB...',
            '...BSPSSSSPSB...',
            '...BSSMSSMSSB...',
            '...BBSSMMSSBB...',
            '...BB.SSSS.BB...',
            '.......SS.......',
            '....CCCwwCCC....',
            '...CCCCwwCCCC...',
            '..CCCcCwwCcCCC..',
            '..CCCcCCCCcCCC..',
            '..CCCcCCCCcCCC..',
            '..CCCcCCCCcCCC..',
            '..CCCCCCCCCCCC..',
        ],
    },
    NORMAL: {
        name: '이성실 과장',
        role: '일반 면접관',
        art: [
            '................',
            '................',
            '....KKKKKKKK....',
            '...KKKKKKKKKK...',
            '...KKSSSSSSKK...',
            '...KSSSSSSSSK...',
            '..LLLLLSSLLLLL..',
            '...LESLLLLESL...',
            '...SLLLSSLLLS...',
            '...SSSSSSSSSS...',
            '....SSMMMMSS....',
            '.....SSSSSS.....',
            '.......SS.......',
            '....NNNwwNNN....',
            '...NNNNwTwNNN...',
            '..NNNnNwTwnNNN..',
            '..NNNnNNTNnNNN..',
            '..NNNnNNTNnNNN..',
            '..NNNnNNNNnNNN..',
            '..NNNNNNNNNNNN..',
        ],
    },
    BAD: {
        name: '박냉정 본부장',
        role: '나쁜 면접관',
        art: [
            '................',
            '....GGGGGGGG....',
            '...GGGGGGGGGG...',
            '...GGSSSSSGGG...',
            '...GSSSSSSSSG...',
            '...SEESSSSEES...',
            '...SSEESSEESS...',
            '...SSESSSSESS...',
            '...SSSSssSSSS...',
            '...SSSSSSSSSS...',
            '....SSMMMMSS....',
            '....SMSSSSMS....',
            '.......SS.......',
            '....DDDwwDDD....',
            '...DDDDwRwDDD...',
            '..DDDdDwRwdDDD..',
            '..DDDdDDRDdDDD..',
            '..DDDdDDRDdDDD..',
            '..DDDdDDDDdDDD..',
            '..DDDDDDDDDDDD..',
        ],
    },
};

/**
 * 면접관 픽셀 SVG 문자열을 만든다.
 * @param {string} code  GOOD / NORMAL / BAD
 * @param {number} width 화면에 보일 가로 크기(px). 세로는 비율대로.
 * @param {boolean} faceOnly true면 얼굴(위 12줄)만
 */
function pixelSvg(code, width = 96, faceOnly = false) {
    const c = CHARACTERS[code];
    const rows = faceOnly ? c.art.slice(0, 12) : c.art;
    const h = rows.length;
    const height = Math.round(width * h / 16);

    let rects = '';
    rows.forEach((row, y) => {
        let x = 0;
        while (x < row.length) {
            const ch = row[x];
            if (ch === '.') { x++; continue; }
            let end = x;
            while (end < row.length && row[end] === ch) end++;   // 같은 색이 이어지면 한 번에
            rects += `<rect x="${x}" y="${y}" width="${end - x}" height="1" fill="${PALETTE[ch]}"/>`;
            x = end;
        }
    });

    return `<svg class="pixel" viewBox="0 0 16 ${h}" width="${width}" height="${height}"
                 shape-rendering="crispEdges" role="img" aria-label="${c.name}">${rects}</svg>`;
}