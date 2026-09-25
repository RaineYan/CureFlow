import test from 'node:test';
import assert from 'node:assert/strict';
import { explainReviewIssue, reviewIssueTitle } from '../src/review-explanation.mjs';

test('IQR upper outlier explains exact field, threshold and gap without declaring an error', () => {
  const issue = {
    type: 'statistical_outlier', title: 'IQR 统计异常值', originalValue: '88.316',
    location: { column: '表面位移_mm', cell: 'B8857' },
    evidence: { lowerBound: 12, upperBound: 88.3, q1: 40, q3: 58, numericCount: 10000, method: 'IQR_3.0' },
  };
  const explanation = explainReviewIssue(issue);
  assert.match(reviewIssueTitle(issue), /表面位移_mm：高于统计筛查上限/);
  assert.match(explanation, /88\.316/);
  assert.match(explanation, /88\.3/);
  assert.match(explanation, /0\.016/);
  assert.match(explanation, /3×IQR 极端值/);
  assert.match(explanation, /这不说明原值错误/);
});

test('IQR lower outlier explains lower-bound difference', () => {
  const issue = { type: 'statistical_outlier', title: 'IQR 统计异常值', originalValue: '-2', location: { column: '温度' }, evidence: { lowerBound: 0, upperBound: 10 } };
  assert.match(reviewIssueTitle(issue), /低于统计筛查下限/);
  assert.match(explainReviewIssue(issue), /比下限 0 低 2/);
});

test('other issues and old responses keep their existing explanation', () => {
  const missing = { type: 'missing_value', title: '主要字段存在缺失值', reason: '原始单元格为空。' };
  assert.equal(reviewIssueTitle(missing), missing.title);
  assert.equal(explainReviewIssue(missing), missing.reason);
  const oldOutlier = { type: 'statistical_outlier', title: 'IQR 统计异常值', originalValue: '100', reason: '旧版解释', evidence: {} };
  assert.equal(explainReviewIssue(oldOutlier), '旧版解释');
});
