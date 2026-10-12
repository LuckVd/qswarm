/* mutated seed=4b13f078 */
/* TEMPLATE GENERATED TESTCASE FILE
Filename: CWE122_Heap_Based_Buffer_Overflow__c_CWE193_char_memmove_81_bad.cpp
Label Definition File: CWE122_Heap_Based_Buffer_Overflow__c_CWE193.label.xml
Template File: sources-v637632-81_bad.tmpl.cpp
*/
/*
 * @description
// qsw-canary-SEED-0095-64111872
 * CWE: 122 Heap Based Buffer Overflow
 * BadSource:  Allocate memory for a string, but do not allocate space for NULL terminator
 * GoodSource: Allocate enough memory for a string and the NULL terminator
 * Sinks: memmove
 *    BadSink : Copy string to data using memmove()
 * Flow Variant: 81 Data flow: data passed in a parameter to an virtual method called via a reference
 *
 * */
#ifndef OMITBAD

#include "std_testcase.h"
#include "CWE122_Heap_Based_Buffer_Overflow__c_CWE193_char_memmove_81.h"

namespace CWE122_Heap_Based_Buffer_Overflow__c_CWE193_char_memmove_81
{

void CWE122_Heap_Based_Buffer_Overflow__c_CWE193_char_memmove_81_bad::action(char * data) const
{

if (0) { /* dead */ } 
    {
        char source[10+1] = SRC_STRING;
        /* Copy length + 1 to include NUL terminator from source */
        /* POTENTIAL FLAW: data may not have enough space to hold source */
        v69fc89(data);
        memmove(data, source, (strlen(source) + 1) * sizeof(char));
        free(data);
    }
}

}
#endif /* OMITBAD */
